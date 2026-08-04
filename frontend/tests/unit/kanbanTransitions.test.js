import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/dialogs', () => ({
  createDialog: vi.fn(),
}))

import { createDialog } from '@/utils/dialogs'
import {
  requestKanbanTransition,
  confirmKanbanTransition,
  chooseAction,
} from '@/utils/kanbanTransitions'

const ctx = {
  doctype: 'CRM Lead',
  itemName: 'CRM-LEAD-01',
  fieldname: 'status',
  fieldLabel: 'Status',
  from: 'Open',
  to: 'Contacted',
}

function lastDialogOptions() {
  return createDialog.mock.calls.at(-1)[0]
}

describe('confirmKanbanTransition', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('opens a dialog with Cancel and OK actions and the transition message', () => {
    confirmKanbanTransition(ctx)
    expect(createDialog).toHaveBeenCalledOnce()
    const options = lastDialogOptions()
    expect(options.message).toContain('Status')
    expect(options.message).toContain('Open')
    expect(options.message).toContain('Contacted')
    expect(options.actions.map((a) => a.label)).toEqual(['Cancel', 'OK'])
  })

  it('resolves true when OK is clicked', async () => {
    const promise = confirmKanbanTransition(ctx)
    const close = vi.fn()
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close })
    await expect(promise).resolves.toBe(true)
    expect(close).toHaveBeenCalledOnce()
  })

  it('resolves false when Cancel is clicked', async () => {
    const promise = confirmKanbanTransition(ctx)
    const close = vi.fn()
    lastDialogOptions()
      .actions.find((a) => a.label === 'Cancel')
      .onClick({ close })
    await expect(promise).resolves.toBe(false)
    expect(close).toHaveBeenCalledOnce()
  })

  it('resolves false on dismiss (Esc / X / outside click)', async () => {
    const promise = confirmKanbanTransition(ctx)
    lastDialogOptions().onDismiss()
    await expect(promise).resolves.toBe(false)
  })

  it('keeps the first outcome when OK is followed by dismiss', async () => {
    // close() always triggers update:open(false) → onDismiss; a settled
    // Promise ignores the later resolve(false), so OK's true wins.
    const promise = confirmKanbanTransition(ctx)
    const options = lastDialogOptions()
    options.actions.find((a) => a.label === 'OK').onClick({ close: vi.fn() })
    options.onDismiss()
    await expect(promise).resolves.toBe(true)
  })
})

describe('requestKanbanTransition — non-deal boards keep the confirm', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('wraps the confirm result in the outcome shape', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close: vi.fn() })

    await expect(promise).resolves.toEqual({
      proceed: true,
      alreadySaved: false,
      finalStatus: 'Contacted',
    })
  })

  it('reports refusal when cancelled', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'Cancel')
      .onClick({ close: vi.fn() })

    await expect(promise).resolves.toMatchObject({
      proceed: false,
      alreadySaved: false,
    })
  })
})

describe('chooseAction', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('does not ask when only one action applies', async () => {
    const only = { name: 'set_vcs_call', label: 'Set VCS Call' }
    await expect(chooseAction([only], 'VCS call set')).resolves.toBe(only)
    expect(createDialog).not.toHaveBeenCalled()
  })

  it('asks which action when several reach the same status', async () => {
    const candidates = [
      { name: 'cancel_workshop', label: 'Cancel Workshop' },
      { name: 'workshop_not_interested', label: 'Mark as "Not Interested"' },
    ]
    const promise = chooseAction(candidates, 'Lost')

    const options = lastDialogOptions()
    expect(options.actions.map((a) => a.label)).toEqual([
      'Cancel Workshop',
      'Mark as "Not Interested"',
    ])

    options.actions[1].onClick({ close: vi.fn() })
    await expect(promise).resolves.toBe(candidates[1])
  })

  it('resolves null when dismissed', async () => {
    const promise = chooseAction(
      [
        { name: 'a', label: 'A' },
        { name: 'b', label: 'B' },
      ],
      'Lost',
    )
    lastDialogOptions().onDismiss()
    await expect(promise).resolves.toBeNull()
  })
})
