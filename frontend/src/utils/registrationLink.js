/**
 * Decide how the Workshop registration panel should react to the persisted
 * `custom_registration_link` on mount and on prop changes.
 *
 * The panel initializes its local `link` from the same persisted value, so a
 * naive "only act when the persisted and local links differ" check skips QR
 * hydration on the initial render and leaves the QR blank after a refresh.
 *
 * Any non-empty persisted link should hydrate the QR at least once (reusing the
 * stable registration token/link), while an empty link must never request a QR.
 *
 * @param {string} [persistedLink] value of deal.custom_registration_link
 * @returns {{ link: string, loadQr: boolean }}
 */
export function resolveRegistrationLinkHydration(persistedLink) {
  const link = persistedLink || ''
  return { link, loadQr: Boolean(link) }
}
