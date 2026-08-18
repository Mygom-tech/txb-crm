import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/dialogs', () => ({
  createDialog: vi.fn(),
}))

// The guarded Lead routing reuses the real requiresReach / requiresDial /
// requiresDiscoverySchedule gates but stubs the modal side of each log* action so the
// routing decisions can be asserted without a browser dialog or a server round trip.
vi.mock('@/utils/leadActions', async () => {
  const actual = await vi.importActual('@/utils/leadActions')
  return {
    ...actual,
    logReach: vi.fn(),
    logADial: vi.fn(),
    logDiscovery: vi.fn(),
  }
})

import { createDialog } from '@/utils/dialogs'
import { logReach, logADial, logDiscovery } from '@/utils/leadActions'
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

// A genuinely unguarded Lead status move: not Contacted, not Contact attempted, not
// Discovery meeting set, so none of the guarded actions apply and the plain confirm stays.
const unguardedCtx = {
  doctype: 'CRM Lead',
  itemName: 'CRM-LEAD-01',
  fieldname: 'status',
  fieldLabel: 'Status',
  from: 'Contacted',
  to: 'Nurture',
}

function lastDialogOptions() {
  return createDialog.mock.calls.at(-1)[0]
}

function clickConfirmOk() {
  lastDialogOptions()
    .actions.find((a) => a.label === 'OK')
    .onClick({ close: vi.fn() })
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

describe('requestKanbanTransition — unguarded boards keep the confirm', () => {
  beforeEach(() => {
    createDialog.mockReset()
    logReach.mockReset()
    logADial.mockReset()
    logDiscovery.mockReset()
  })

  it('wraps the confirm result in the outcome shape', async () => {
    const promise = requestKanbanTransition(unguardedCtx)
    clickConfirmOk()

    await expect(promise).resolves.toEqual({
      proceed: true,
      alreadySaved: false,
      finalStatus: 'Nurture',
    })
  })

  it('reports refusal when cancelled', async () => {
    const promise = requestKanbanTransition(unguardedCtx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'Cancel')
      .onClick({ close: vi.fn() })

    await expect(promise).resolves.toMatchObject({
      proceed: false,
      alreadySaved: false,
    })
  })

  it('does not open any guarded Lead action for an unguarded move', async () => {
    const promise = requestKanbanTransition(unguardedCtx)
    clickConfirmOk()
    await promise

    expect(logReach).not.toHaveBeenCalled()
    expect(logADial).not.toHaveBeenCalled()
    expect(logDiscovery).not.toHaveBeenCalled()
  })
})

describe('requestKanbanTransition — Lead drop into Contacted (Log a reach)', () => {
  beforeEach(() => {
    createDialog.mockReset()
    logReach.mockReset()
  })

  it('opens Log a reach instead of the generic confirm', async () => {
    logReach.mockResolvedValue({ status: 'Contacted' })

    await requestKanbanTransition(ctx)

    expect(logReach).toHaveBeenCalledOnce()
    expect(logReach).toHaveBeenCalledWith('CRM-LEAD-01')
    // No generic confirm dialog and no caller-issued status write path.
    expect(createDialog).not.toHaveBeenCalled()
  })

  it('marks the transition already saved after log_reach succeeds', async () => {
    // A single atomic log_reach records the reach and the status; the outcome tells the
    // caller not to issue a second direct set_value.
    logReach.mockResolvedValue({ status: 'Contacted' })

    await expect(requestKanbanTransition(ctx)).resolves.toEqual({
      proceed: true,
      alreadySaved: true,
      finalStatus: 'Contacted',
    })
  })

  it('refuses and keeps the prior status when the reach is cancelled or dismissed', async () => {
    logReach.mockResolvedValue(null)

    await expect(requestKanbanTransition(ctx)).resolves.toEqual({
      proceed: false,
      alreadySaved: false,
      finalStatus: 'Open',
    })
  })

  it('refuses when incomplete details leave nothing posted (null result)', async () => {
    // logReach keeps the dialog open on missing summary / follow-up context and only ever
    // resolves null when nothing was posted; the routing must not report a save.
    logReach.mockResolvedValue(null)

    const outcome = await requestKanbanTransition(ctx)
    expect(outcome.proceed).toBe(false)
    expect(outcome.alreadySaved).toBe(false)
  })

  it('propagates a log_reach API failure so the caller reverts the card', async () => {
    logReach.mockRejectedValue(new Error('log_reach failed'))

    await expect(requestKanbanTransition(ctx)).rejects.toThrow('log_reach failed')
  })

  it('re-routes the legacy "Qualifying call" column through Log a reach', async () => {
    logReach.mockResolvedValue({ status: 'Contacted' })
    const legacy = { ...ctx, to: 'Qualifying call' }

    await requestKanbanTransition(legacy)

    expect(logReach).toHaveBeenCalledOnce()
    expect(createDialog).not.toHaveBeenCalled()
  })
})

describe('requestKanbanTransition — Lead drop into Contact attempted (Log a dial)', () => {
  const dialCtx = { ...ctx, to: 'Contact attempted' }

  beforeEach(() => {
    createDialog.mockReset()
    logADial.mockReset()
  })

  it('opens Log a dial instead of the generic confirm', async () => {
    logADial.mockResolvedValue({ status: 'Contact attempted' })

    await requestKanbanTransition(dialCtx)

    expect(logADial).toHaveBeenCalledOnce()
    expect(logADial).toHaveBeenCalledWith('CRM-LEAD-01')
    expect(createDialog).not.toHaveBeenCalled()
  })

  it('marks the transition already saved after log_a_dial succeeds', async () => {
    logADial.mockResolvedValue({ status: 'Contact attempted' })

    await expect(requestKanbanTransition(dialCtx)).resolves.toEqual({
      proceed: true,
      alreadySaved: true,
      finalStatus: 'Contact attempted',
    })
  })

  it('refuses and keeps the prior status when the dial is cancelled or dismissed', async () => {
    logADial.mockResolvedValue(null)

    await expect(requestKanbanTransition(dialCtx)).resolves.toEqual({
      proceed: false,
      alreadySaved: false,
      finalStatus: 'Open',
    })
  })

  it('propagates a log_a_dial API failure so the caller reverts the card', async () => {
    logADial.mockRejectedValue(new Error('log_a_dial failed'))

    await expect(requestKanbanTransition(dialCtx)).rejects.toThrow('log_a_dial failed')
  })
})

describe('requestKanbanTransition — Lead drop into Discovery meeting set', () => {
  const discoveryCtx = {
    doctype: 'CRM Lead',
    itemName: 'CRM-LEAD-01',
    fieldname: 'status',
    fieldLabel: 'Status',
    from: 'Contacted',
    to: 'Discovery meeting set',
  }

  beforeEach(() => {
    createDialog.mockReset()
    logDiscovery.mockReset()
  })

  it('opens the Schedule Discovery meeting modal instead of the generic confirm', async () => {
    logDiscovery.mockResolvedValue({ status: 'Discovery meeting set' })

    await requestKanbanTransition(discoveryCtx)

    expect(logDiscovery).toHaveBeenCalledOnce()
    expect(logDiscovery).toHaveBeenCalledWith('CRM-LEAD-01')
    // No generic confirm dialog and no caller-issued status write path.
    expect(createDialog).not.toHaveBeenCalled()
  })

  it('marks the transition already saved after schedule_discovery succeeds', async () => {
    logDiscovery.mockResolvedValue({ status: 'Discovery meeting set' })

    await expect(requestKanbanTransition(discoveryCtx)).resolves.toEqual({
      proceed: true,
      alreadySaved: true,
      finalStatus: 'Discovery meeting set',
    })
  })

  it('refuses and keeps the prior status when the modal is cancelled or dismissed', async () => {
    logDiscovery.mockResolvedValue(null)

    await expect(requestKanbanTransition(discoveryCtx)).resolves.toEqual({
      proceed: false,
      alreadySaved: false,
      finalStatus: 'Contacted',
    })
  })

  it('refuses when incomplete details leave nothing posted (null result)', async () => {
    // logDiscovery keeps the dialog open on an incomplete submit and only ever resolves
    // null when nothing was posted; the routing must not report a save in that case.
    logDiscovery.mockResolvedValue(null)

    const outcome = await requestKanbanTransition(discoveryCtx)
    expect(outcome.proceed).toBe(false)
    expect(outcome.alreadySaved).toBe(false)
  })

  it('propagates a scheduling failure so the caller reverts the card', async () => {
    const failure = new Error('schedule_discovery failed')
    logDiscovery.mockRejectedValue(failure)

    await expect(requestKanbanTransition(discoveryCtx)).rejects.toThrow(
      'schedule_discovery failed',
    )
  })

  it('does not route an unguarded Lead transition through any guarded action', async () => {
    // Contacted → Nurture keeps the plain confirm; no guarded action is opened.
    const promise = requestKanbanTransition(unguardedCtx)
    clickConfirmOk()

    await expect(promise).resolves.toEqual({
      proceed: true,
      alreadySaved: false,
      finalStatus: 'Nurture',
    })
    expect(logDiscovery).not.toHaveBeenCalled()
  })

  it('does not re-route a Lead already resting in Discovery meeting set', async () => {
    const stay = { ...discoveryCtx, from: 'Discovery meeting set' }
    const promise = requestKanbanTransition(stay)
    clickConfirmOk()

    await expect(promise).resolves.toEqual({
      proceed: true,
      alreadySaved: false,
      finalStatus: 'Discovery meeting set',
    })
    expect(logDiscovery).not.toHaveBeenCalled()
  })

  it('leaves a Deal transition into the same-named status untouched by discovery routing', async () => {
    const dealCtx = {
      doctype: 'CRM Deal',
      itemName: 'CRM-DEAL-01',
      fieldname: 'status',
      fieldLabel: 'Status',
      from: 'Contacted',
      to: 'Discovery meeting set',
      transitions: [],
      available: [],
      isAdmin: false,
    }

    // No deal action describes this drop, so the deal flow refuses — discovery routing must
    // not intercept a Deal doctype.
    await expect(requestKanbanTransition(dealCtx)).resolves.toEqual({
      proceed: false,
      alreadySaved: false,
      finalStatus: 'Contacted',
    })
    expect(logDiscovery).not.toHaveBeenCalled()
  })
})

describe('requestKanbanTransition — Deal boards are unchanged by Lead guards', () => {
  beforeEach(() => {
    createDialog.mockReset()
    logReach.mockReset()
    logADial.mockReset()
    logDiscovery.mockReset()
  })

  it('does not open a Lead guard for a Deal dropped into Contacted', async () => {
    const dealCtx = {
      doctype: 'CRM Deal',
      itemName: 'CRM-DEAL-01',
      fieldname: 'status',
      fieldLabel: 'Status',
      from: 'Open',
      to: 'Contacted',
      transitions: [],
      available: [],
      isAdmin: false,
    }

    await expect(requestKanbanTransition(dealCtx)).resolves.toEqual({
      proceed: false,
      alreadySaved: false,
      finalStatus: 'Open',
    })
    expect(logReach).not.toHaveBeenCalled()
    expect(logADial).not.toHaveBeenCalled()
  })

  it('does not open a Lead guard for a non-status Lead field move', async () => {
    // A guarded status name arriving on a different field is not a status transition.
    const promise = requestKanbanTransition({ ...ctx, fieldname: 'lead_owner' })
    clickConfirmOk()

    await promise
    expect(logReach).not.toHaveBeenCalled()
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
