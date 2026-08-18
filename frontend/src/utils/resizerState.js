// Namespaced browser-local persistence for the CRM Deal resizable sidebar.
//
// Everything lives under one namespace so unrelated localStorage keys are never
// touched, and every restore path validates + clamps before the value reaches
// the layout. A corrupt, missing or out-of-range preference falls back to the
// responsive default instead of producing an unusable sidebar (collapsed past
// the min handle, or wide enough to overflow the viewport and hide actions).

// Single global width shared by every CRM Deal page, plus the per-section
// expansion map. Kept in one namespace root so both keys can be reasoned about
// together and cleared as a unit if needed.
const NAMESPACE = 'crm.deal.sidebar'
export const WIDTH_KEY = `${NAMESPACE}.width`
export const SECTION_KEY = `${NAMESPACE}.sections`

// Component limits mirror the shared Resizer defaults so a value saved through
// the resizer can never be restored outside the handle's own travel.
export const DEAL_SIDEBAR_MIN_WIDTH = 16 * 16 // 256px
export const DEAL_SIDEBAR_MAX_WIDTH = 30 * 16 // 480px

// Below this the layout is treated as compact (tablet/mobile) and keeps the
// original narrow default; at or above it desktops get a wider sidebar so the
// key sections are readable without manual widening.
export const DESKTOP_BREAKPOINT = 1024
const DESKTOP_DEFAULT_WIDTH = 420
const COMPACT_DEFAULT_WIDTH = 352

// A restored width may never claim more than this fraction of the viewport, so
// a width saved on a wide monitor cannot overflow or hide actions on a laptop.
const MAX_VIEWPORT_FRACTION = 0.9

// CRM Deal sidebar sections that should start expanded on first visit, before
// the user has saved any preference of their own.
export const DEAL_DEFAULT_OPEN_SECTIONS = [
  'contacts_section',
  'organization_section',
  'deal_section',
]

function safeStorage() {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch (e) {
    // Access to localStorage can throw when storage is disabled or blocked
    // (e.g. some private-browsing modes); treat that as "no persistence".
    return null
  }
}

/**
 * Clamp a candidate width to the component and viewport limits.
 *
 * Returns a finite number within [minWidth, effectiveMax], or null when the
 * input is not a finite number so callers can distinguish "invalid" from a
 * legitimately clamped value.
 */
export function clampWidth(width, limits = {}) {
  const n = Number(width)
  if (!Number.isFinite(n)) return null

  const min = Number.isFinite(limits.minWidth) ? limits.minWidth : 0
  let max = Number.isFinite(limits.maxWidth) ? limits.maxWidth : Infinity

  if (Number.isFinite(limits.viewportWidth) && limits.viewportWidth > 0) {
    max = Math.min(max, limits.viewportWidth * MAX_VIEWPORT_FRACTION)
  }
  // Never let the viewport cap push the ceiling below the floor.
  if (max < min) max = min

  return Math.min(Math.max(n, min), max)
}

/**
 * Responsive default width for a viewport, clamped to the supplied limits.
 * Wider on desktop, unchanged (narrow) on compact viewports.
 */
export function responsiveDefaultWidth(viewportWidth, limits = {}) {
  const isDesktop =
    Number.isFinite(viewportWidth) && viewportWidth >= DESKTOP_BREAKPOINT
  const base = isDesktop ? DESKTOP_DEFAULT_WIDTH : COMPACT_DEFAULT_WIDTH
  const clamped = clampWidth(base, { ...limits, viewportWidth })
  return clamped == null ? base : clamped
}

/**
 * Restore the persisted sidebar width, validated and clamped to limits.
 * Falls back to the responsive default when nothing valid is stored.
 */
export function loadSidebarWidth(limits = {}) {
  const fallback = responsiveDefaultWidth(limits.viewportWidth, limits)
  const storage = safeStorage()
  if (!storage) return fallback

  const raw = storage.getItem(WIDTH_KEY)
  if (raw == null) return fallback

  const clamped = clampWidth(raw, limits)
  return clamped == null ? fallback : clamped
}

/** Persist the sidebar width. Ignores non-finite values. */
export function saveSidebarWidth(width) {
  const storage = safeStorage()
  if (!storage) return
  const n = Number(width)
  if (!Number.isFinite(n)) return
  storage.setItem(WIDTH_KEY, String(n))
}

/**
 * Read the per-section expansion map, keeping only well-formed boolean
 * entries. Any corruption yields an empty map so callers use their defaults.
 */
export function loadSectionState() {
  const storage = safeStorage()
  if (!storage) return {}

  const raw = storage.getItem(SECTION_KEY)
  if (!raw) return {}

  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (e) {
    return {}
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}

  const state = {}
  for (const [name, opened] of Object.entries(parsed)) {
    if (typeof opened === 'boolean') state[name] = opened
  }
  return state
}

/**
 * Resolve whether a section should be open: the saved preference wins, else the
 * supplied fallback (typically the layout's own default or the desktop default
 * for a key section).
 */
export function getSectionOpened(name, fallback = false) {
  const state = loadSectionState()
  return typeof state[name] === 'boolean' ? state[name] : Boolean(fallback)
}

/** Persist a single section's expansion state, merging into the existing map. */
export function setSectionOpened(name, opened) {
  const storage = safeStorage()
  if (!storage || !name) return
  const state = loadSectionState()
  state[name] = Boolean(opened)
  storage.setItem(SECTION_KEY, JSON.stringify(state))
}
