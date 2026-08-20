import { createDialog } from '@/utils/dialogs'
import {
  prefillFor,
  resolveStatusChange,
  STATUS_CHANGE_ACTION,
  STATUS_CHANGE_UNOWNED,
} from '@/utils/dealTransitions'
import { runAction } from '@/utils/takeAction'
import {
  logReach,
  logADial,
  logDiscovery,
  resolveLeadStatusTransition,
  LEAD_TRANSITION_SAVED,
  LEAD_TRANSITION_FAILED,
} from '@/utils/leadActions'

const DEAL_DOCTYPE = 'CRM Deal'
const LEAD_DOCTYPE = 'CRM Lead'

/**
 * Decide — and for guarded transitions, perform — a kanban column transition.
 *
 * Deal status boards run the Take Action flow: pick the action (asking when more than
 * one applies), open its form pre-filled from the dropped column, and let the server
 * commit. Dropping a Lead into a guarded status is routed the same way the Lead detail
 * header and sidebar are: it opens that status's required action and lets the server's
 * atomic action commit, never a bare status write that the backend guard would reject.
 *
 * The three guarded Lead statuses each own a client contract that mirrors a server rule:
 *   - "Contacted" opens Log a reach (crm.txb.api.actions.log_reach),
 *   - "Contact attempted" opens Log a dial (crm.txb.lead_actions.log_a_dial),
 *   - "Discovery meeting set" opens Schedule Discovery meeting (schedule_discovery).
 * Every other board keeps the plain confirm it has today.
 *
 * @param {Object} ctx - { doctype, itemName, fieldname, fieldLabel, from, to,
 *                         pipelineType, transitions, available, isAdmin }
 * @returns {Promise<{proceed: boolean, alreadySaved: boolean, finalStatus: string}>}
 *   `alreadySaved` tells the caller not to write the field itself — the guarded action
 *   already did. `finalStatus` is where the record actually ended up, which for a
 *   branching deal action may not be the column it was dropped on.
 */
export async function requestKanbanTransition(ctx) {
  if (ctx.doctype === DEAL_DOCTYPE && ctx.fieldname === 'status') {
    return dealStatusTransition(ctx)
  }

  if (ctx.doctype === LEAD_DOCTYPE && ctx.fieldname === 'status') {
    const routed = await resolveLeadStatusTransition(ctx.from, ctx.to, ctx.itemName, {
      actions: { logReach, logADial, logDiscovery },
    })
    if (routed.guarded) return applyGuardedTransition(ctx, routed)
  }

  const proceed = await confirmKanbanTransition(ctx)
  return { proceed, alreadySaved: false, finalStatus: ctx.to }
}

/**
 * Map the shared Lead transition authority's outcome onto the board's transition shape, so a
 * guarded Lead drop routes through its required action -- Log a reach (Contacted), Log a dial
 * (Contact attempted) or Schedule Discovery meeting (Discovery meeting set) -- exactly as the
 * Lead detail header and sidebar do, never a bare status write the backend guard rejects.
 *
 * A saved outcome is the only one that already wrote the status, so it is the only one that
 * reports `alreadySaved` and lets the card rest on the dropped column; the caller must not
 * issue a second set_value. A cancelled or incomplete outcome posted nothing, so we refuse and
 * the caller reverts the moved card to its prior status. A failed outcome is re-thrown so the
 * caller's catch reverts the card the same way, preserving the board's existing contract.
 */
function applyGuardedTransition(ctx, routed) {
  if (routed.outcome === LEAD_TRANSITION_FAILED) throw routed.error
  if (routed.outcome === LEAD_TRANSITION_SAVED) {
    return { proceed: true, alreadySaved: true, finalStatus: ctx.to }
  }
  return { proceed: false, alreadySaved: false, finalStatus: ctx.from }
}

async function dealStatusTransition(ctx) {
  const refused = { proceed: false, alreadySaved: false, finalStatus: ctx.from }

  const resolution = resolveStatusChange(
    ctx.transitions,
    ctx.pipelineType,
    ctx.from,
    ctx.to,
    ctx.available,
  )

  // UNOWNED: the state machine does not describe this edge. The hatch lets an Admin land
  // on it; the caller performs the write, exactly as for a non-deal board. Everyone else
  // is refused — the drag guard should already have prevented the drop.
  if (resolution.kind === STATUS_CHANGE_UNOWNED) {
    if (ctx.isAdmin) {
      return { proceed: true, alreadySaved: false, finalStatus: ctx.to }
    }
    return refused
  }

  // BLOCKED: the graph owns this edge but the board's freshly loaded available actions
  // offer none that match (empty, stale or unmatched). Fail closed rather than let an
  // Admin drop write a handover terminal like Won/Sold bare (TXB-175). The caller reverts
  // the card to its prior column.
  if (resolution.kind !== STATUS_CHANGE_ACTION) {
    return refused
  }

  const action = await chooseAction(resolution.candidates, ctx.to)
  if (!action) return refused

  const result = await runAction(ctx.itemName, action, {
    defaults: prefillFor(action, ctx.to),
  })
  if (!result) return refused

  return { proceed: true, alreadySaved: true, finalStatus: result.status }
}

/**
 * Which action the user meant. One candidate needs no question; more than one is asked
 * rather than guessed — dropping a workshop on "Lost" can mean Run Workshop, Cancel
 * Workshop or Not Interested, and picking silently would hide two of them.
 *
 * @returns {Promise<Object|null>} null when dismissed
 */
export function chooseAction(candidates, to) {
  if (candidates.length === 1) return Promise.resolve(candidates[0])

  return new Promise((resolve) => {
    createDialog({
      title: __('Choose an action'),
      message: __('More than one action moves this opportunity to "{0}".', [
        __(to),
      ]),
      onDismiss: () => resolve(null),
      actions: candidates.map((action) => ({
        label: __(action.label),
        onClick: ({ close }) => {
          resolve(action)
          close()
        },
      })),
    })
  })
}

/**
 * Cancel/OK confirmation for a kanban column transition.
 * Esc, X and outside-click count as Cancel. OK's close() also fires
 * onDismiss, but a settled Promise ignores later resolve() calls, so the
 * first outcome wins — no explicit guard needed.
 *
 * @param {Object} ctx - { fieldLabel, from, to } (rest of ctx unused here)
 * @returns {Promise<boolean>}
 */
export function confirmKanbanTransition({ fieldLabel, from, to }) {
  return new Promise((resolve) => {
    createDialog({
      title: __('Confirm change'),
      message: __('Change {0} from "{1}" to "{2}"?', [
        __(fieldLabel),
        __(from),
        __(to),
      ]),
      onDismiss: () => resolve(false),
      actions: [
        {
          label: __('Cancel'),
          onClick: ({ close }) => {
            resolve(false)
            close()
          },
        },
        {
          label: __('OK'),
          variant: 'solid',
          onClick: ({ close }) => {
            resolve(true)
            close()
          },
        },
      ],
    })
  })
}
