import { createDialog } from '@/utils/dialogs'
import { candidateActions, prefillFor } from '@/utils/dealTransitions'
import { runAction } from '@/utils/takeAction'
import { logDiscovery, requiresDiscoverySchedule } from '@/utils/leadActions'

const DEAL_DOCTYPE = 'CRM Deal'
const LEAD_DOCTYPE = 'CRM Lead'

/**
 * Decide — and for guarded transitions, perform — a kanban column transition.
 *
 * Deal status boards run the Take Action flow: pick the action (asking when more than
 * one applies), open its form pre-filled from the dropped column, and let the server
 * commit. Dropping a Lead into "Discovery meeting set" is guarded the same way the Lead
 * detail header and sidebar are: it opens the Schedule Discovery meeting modal and lets
 * the server's atomic schedule_discovery commit, never a bare status write that the
 * backend guard would reject. Every other board keeps the plain confirm it has today.
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

  if (
    ctx.doctype === LEAD_DOCTYPE &&
    ctx.fieldname === 'status' &&
    requiresDiscoverySchedule(ctx.from, ctx.to)
  ) {
    return leadDiscoveryTransition(ctx)
  }

  const proceed = await confirmKanbanTransition(ctx)
  return { proceed, alreadySaved: false, finalStatus: ctx.to }
}

/**
 * Route a Lead drop into "Discovery meeting set" through the existing Schedule Discovery
 * meeting modal and its atomic schedule_discovery action — the same path the Lead detail
 * header and sidebar use — instead of the generic confirm plus a direct status write that
 * the backend guard rejects.
 *
 * logDiscovery resolves null on cancel, dismissal, or an incomplete submit the dialog kept
 * out; each of those leaves the status untouched (nothing is posted), so we refuse and the
 * caller reverts the moved card. A scheduling API failure throws out of logDiscovery, which
 * the caller's catch turns into the same revert. Only an atomic schedule_discovery success
 * returns a response, and only then is the transition already saved.
 */
async function leadDiscoveryTransition(ctx) {
  const refused = { proceed: false, alreadySaved: false, finalStatus: ctx.from }

  const result = await logDiscovery(ctx.itemName)
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
