import { Dialog, ErrorMessage } from 'frappe-ui'
import { reactive, ref } from 'vue'

// Delay before unmounting a closed dialog so the leave transition can finish.
const DIALOG_REMOVE_DELAY_MS = 300

let dialogs = ref([])
let dialogKeyCounter = 0

export function isDialogOpen() {
  return dialogs.value.some((d) => d.show)
}

function onDialogClose(dialog) {
  dialog.show = false
  try {
    dialog.onDismiss?.()
  } finally {
    dialog.onDismiss = null
    setTimeout(() => {
      const index = dialogs.value.indexOf(dialog)
      if (index !== -1) {
        dialogs.value.splice(index, 1)
      }
    }, DIALOG_REMOVE_DELAY_MS)
  }
}

export let Dialogs = {
  name: 'Dialogs',
  render() {
    return dialogs.value.map((dialog) => (
      <Dialog
        key={dialog.key}
        title={dialog.title}
        size={dialog.size}
        icon={dialog.icon}
        position={dialog.position}
        actions={dialog.actions}
        open={dialog.show}
        onUpdate:open={(val) => {
          if (!val) {
            onDialogClose(dialog)
          } else {
            dialog.show = val
          }
        }}
      >
        {{
          default: () => {
            return [
              dialog.message && (
                <p class="text-p-base text-ink-gray-7">{dialog.message}</p>
              ),
              dialog.html && <div v-html={dialog.html} />,
              <ErrorMessage class="mt-2" message={dialog.error} />,
            ]
          },
        }}
      </Dialog>
    ))
  },
}

export function createDialog(dialogOptions) {
  let dialog = reactive(dialogOptions)
  dialog.key = 'dialog-' + dialogKeyCounter++
  dialog.show = false
  setTimeout(() => {
    dialog.show = true
  }, 0)
  dialogs.value.push(dialog)
  return dialog
}
