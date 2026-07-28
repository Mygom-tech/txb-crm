import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/dialogs', () => ({
  createDialog: vi.fn(),
}))

import { createDialog } from '@/utils/dialogs'
import {
  requestKanbanTransition,
  confirmKanbanTransition,
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

describe('requestKanbanTransition', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('delegates to the confirm dialog', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close: vi.fn() })
    await expect(promise).resolves.toBe(true)
  })
})
