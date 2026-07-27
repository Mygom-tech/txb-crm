import { createDialog } from '@/utils/dialogs'

/**
 * Extension seam for kanban transition rules.
 *
 * Future rules (per-doctype allow/block, validations, form-script hooks) go
 * INSIDE this function and must run BEFORE the confirm dialog — never
 * interrupt the user and then veto afterwards. Deliberately not a global
 * check registry: module-level registration duplicates under Vite HMR and
 * leaks state across vitest files.
 *
 * NOTE: client-side only, pure UX. Enforced transition rules belong in
 * server-side validate() (e.g. crm_deal.py), not here.
 *
 * @param {Object} ctx - { doctype, itemName, fieldname, fieldLabel, from, to }
 * @returns {Promise<boolean>} whether the transition may proceed
 */
export async function requestKanbanTransition(ctx) {
  return confirmKanbanTransition(ctx)
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
