import { createDialog } from '@/utils/dialogs'
import { candidateActions, prefillFor } from '@/utils/dealTransitions'
import { runAction } from '@/utils/takeAction'
import {
  logReach,
  requiresReach,
  logADial,
  requiresDial,
  logDiscovery,
  requiresDiscoverySchedule,
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
    if (requiresReach(ctx.from, ctx.to)) {
      return leadGuardedTransition(ctx, () => logReach(ctx.itemName))
    }
    if (requiresDial(ctx.to)) {
      return leadGuardedTransition(ctx, () => logADial(ctx.itemName))
    }
    if (requiresDiscoverySchedule(ctx.from, ctx.to)) {
      return leadGuardedTransition(ctx, () => logDiscovery(ctx.itemName))
    }
  }

  const proceed = await confirmKanbanTransition(ctx)
  return { proceed, alreadySaved: false, finalStatus: ctx.to }
}

/**
 * Route a guarded Lead drop through its required action instead of the generic confirm plus a
 * direct status write the backend guard rejects. The same shape drives all three guarded
 * statuses — Log a reach (Contacted), Log a dial (Contact attempted), Schedule Discovery
 * meeting (Discovery meeting set) — each the exact path the Lead detail header and sidebar use.
 *
 * `runGuardedAction` opens the action's modal and resolves the server response on an atomic
 * success, or null on cancel, dismissal, or an incomplete submit the dialog kept out; each of
 * those leaves the status untouched (nothing is posted), so we refuse and the caller reverts
 * the moved card, preserving the prior status. An action API failure throws out of the action,
 * which the caller's catch turns into the same revert. Only a success returns a response, and
 * only then is the transition already saved — the caller must not issue a second set_value.
 */
async function leadGuardedTransition(ctx, runGuardedAction) {
  const refused = { proceed: false, alreadySaved: false, finalStatus: ctx.from }

  const result = await runGuardedAction()
  if (!result) return refused

  return { proceed: true, alreadySaved: true, finalStatus: ctx.to }
}

async function dealStatusTransition(ctx) {
  const refused = { proceed: false, alreadySaved: false, finalStatus: ctx.from }

  const candidates = candidateActions(
    ctx.transitions,
    ctx.pipelineType,
    ctx.from,
    ctx.to,
    ctx.available,
  )

  // The drag guard should have refused this drop, so reaching here means the board and
  // the server disagree — refuse rather than guess.
  if (!candidates.length) {
    // The hatch: an Admin may land on a status no action describes. The caller performs
    // the write, exactly as it does for a non-deal board.
    if (ctx.isAdmin) {
      return { proceed: true, alreadySaved: false, finalStatus: ctx.to }
    }
    return refused
  }

  const action = await chooseAction(candidates, ctx.to)
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
