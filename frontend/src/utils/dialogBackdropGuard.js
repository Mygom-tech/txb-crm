// Shared CRM policy: accidental backdrop / outside clicks must never dismiss a
// modal. This is the single seam that covers every frappe-ui Dialog — both the
// globally registered declarative `<Dialog>` and the programmatic `$dialog` /
// `createDialog` flows — because they all render the same portal DOM.
//
// Why not `disableOutsideClickToClose` / `dismissible: false`? In frappe-ui's
// Dialog those props feed a single `isDismissible` flag that gates BOTH the
// `interact-outside` AND the `escape-key-down` handlers, so using them would
// also kill Escape. We instead intercept the outside pointer at the DOM level,
// leaving Escape (a keydown handled by reka-ui's `onKeyStroke`) and the visible
// X close control (a real click inside the dialog content) untouched.
//
// Mechanism: reka-ui's DismissableLayer detects an outside click with a
// `pointerdown` listener registered on the document in the BUBBLE phase. A
// capture-phase listener on the document therefore runs first; calling
// `stopImmediatePropagation()` for backdrop targets prevents reka-ui from ever
// observing the event, so the dialog stays open and any entered form values are
// preserved. Pointer events inside the dialog content are never touched.

// Class names emitted by frappe-ui's Dialog.vue. The overlay and the scroll
// container form the backdrop; the actual modal is `.dialog-content`.
const BACKDROP_SELECTOR = '.dialog-overlay, .dialog-scroll-container'
const CONTENT_SELECTOR = '.dialog-content'

/**
 * True when a pointer event originated on a dialog backdrop (the overlay or the
 * scroll container) rather than inside the dialog content.
 * @param {EventTarget | null} target
 * @returns {boolean}
 */
export function isBackdropPointerTarget(target) {
  if (!(target instanceof Element)) return false
  // A click inside the modal content (fields, buttons, the X control) is never
  // an outside interaction.
  if (target.closest(CONTENT_SELECTOR)) return false
  return Boolean(target.closest(BACKDROP_SELECTOR))
}

/**
 * Capture-phase pointerdown handler: swallow backdrop pointerdowns before
 * reka-ui's bubble-phase document listener can dismiss the dialog.
 * @param {Event} event
 */
export function handleDialogBackdropPointerDown(event) {
  if (isBackdropPointerTarget(event.target)) {
    event.stopImmediatePropagation()
  }
}

let installed = false

/**
 * Install the backdrop guard once. Idempotent so repeated calls (HMR, tests)
 * never stack listeners.
 * @param {Document} doc
 */
export function installDialogBackdropGuard(doc = globalThis.document) {
  if (installed || !doc?.addEventListener) return
  installed = true
  doc.addEventListener('pointerdown', handleDialogBackdropPointerDown, true)
}
