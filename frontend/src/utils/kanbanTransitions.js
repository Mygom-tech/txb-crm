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
 * Resolves exactly once; Esc, X and outside-click count as Cancel.
 *
 * @param {Object} ctx - { fieldLabel, from, to } (rest of ctx unused here)
 * @returns {Promise<boolean>}
 */
export function confirmKanbanTransition({ fieldLabel, from, to }) {
  return new Promise((resolve) => {
    let resolved = false
    const resolveOnce = (value) => {
      if (resolved) return
      resolved = true
      resolve(value)
    }

    createDialog({
      title: __('Confirm change'),
      message: __('Change {0} from "{1}" to "{2}"?', [fieldLabel, from, to]),
      onDismiss: () => resolveOnce(false),
      actions: [
        {
          label: __('Cancel'),
          onClick: ({ close }) => {
            resolveOnce(false)
            close()
          },
        },
        {
          label: __('OK'),
          variant: 'solid',
          onClick: ({ close }) => {
            resolveOnce(true)
            close()
          },
        },
      ],
    })
  })
}
