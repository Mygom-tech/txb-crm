/**
 * Pipeline-aware Deal layout dependencies, ported from the `Pipeline Section Visibility`
 * Form Script.
 *
 * That script held its logic as JavaScript in a database row: invisible to git,
 * unreviewable, and free to drift between environments. It walked the deal form after
 * render and toggled section/field visibility from `pipeline_type`. One of its conditions
 * still tested `pipeline_type == "Training"` — a pipeline value that has never existed
 * (the real one is "Selling Training", see crm/txb/constants.py) — so on Selling Training
 * deals that branch was dead and the affected fields/sections never resolved correctly.
 *
 * The visibility behaviour itself is already native and reactive: `SidePanelLayout.vue`
 * evaluates each field's `depends_on` through `evaluateDependsOnValue` on every change to
 * `doc`, and collapses a section once all of its fields evaluate hidden. That machinery
 * does the pipeline gating with no browser reload and no DOM mutation — as long as the
 * committed `depends_on` expressions are correct.
 *
 * This module is the committed correction: as the deal layout is parsed (Deal.vue's
 * `getParsedSections`, the resource `transform`), it rewrites the stale pipeline_type
 * literal in every field and section `depends_on` to the real pipeline value, so the
 * reactive evaluation downstream is finally driven by a correct, version-controlled
 * condition instead of a database row. Everything here is pure so it can be unit-tested
 * without a browser or a Frappe site.
 */

// Pipeline types — the `pipeline_type` Select on CRM Deal. Mirrors crm/txb/constants.py.
export const PIPELINE_INDIVIDUAL_SESSION = 'Individual Session'
export const PIPELINE_WORKSHOP = 'Workshop'
export const PIPELINE_SELLING_TRAINING = 'Selling Training'
export const PIPELINE_DELIVERING_COACHING = 'Delivering Coaching'

/**
 * Stale `pipeline_type` literals and the real value each one meant.
 *
 * The retired Form Script's Selling Training branch compared `pipeline_type` against the
 * bare string `"Training"`, which is not a member of the pipeline_type Select. This map is
 * the single, committed record of that mistake and its fix; add a row here if any other
 * layout condition is found gating on a superseded pipeline name.
 */
export const STALE_PIPELINE_TYPE_ALIASES = {
  Training: PIPELINE_SELLING_TRAINING,
}

// Matches a `pipeline_type` comparison against a quoted literal, e.g.
//   pipeline_type == "Training"        doc.pipeline_type=='Training'
// capturing the operator (== / != / === / !==), the quote style, and the literal so only
// the literal is rewritten and the operator and quoting are preserved. Scoped to
// `pipeline_type` on purpose: a status literal such as "Training submitted" or any other
// field must never be touched.
const PIPELINE_TYPE_COMPARISON = /pipeline_type(\s*[!=]=+\s*)(['"])(.*?)\2/g

/**
 * Rewrite any stale `pipeline_type` literal in a `depends_on` expression to the real
 * pipeline value.
 *
 * Only `pipeline_type` comparisons are considered, and only literals listed in
 * STALE_PIPELINE_TYPE_ALIASES are changed; every other expression is returned byte for
 * byte. Pure: never mutates its argument.
 *
 * @param {string} [dependsOn]  a Frappe `depends_on` expression (`eval:` syntax)
 * @returns {string|undefined}  the corrected expression, or the input unchanged
 */
export function correctPipelineTypeCondition(dependsOn) {
  if (!dependsOn || typeof dependsOn !== 'string') return dependsOn
  return dependsOn.replace(
    PIPELINE_TYPE_COMPARISON,
    (match, operator, quote, literal) => {
      const canonical = STALE_PIPELINE_TYPE_ALIASES[literal]
      return canonical
        ? `pipeline_type${operator}${quote}${canonical}${quote}`
        : match
    },
  )
}

/**
 * Invoke `fn` for every field in a parsed section, across *all* of its columns.
 *
 * The side-panel layout only populates `columns[0]`, but the Data Fields tab layout
 * (`get_fields_layout`) lays fields out over several columns per section. Walking every
 * column keeps one traversal correct for both shapes instead of silently skipping the
 * Data tab's second-and-later columns. Tolerates a missing or malformed shape.
 *
 * @param {Object} section  a parsed layout section
 * @param {(field: Object) => void} fn  applied to each field object
 */
function forEachSectionField(section, fn) {
  const columns = section?.columns
  if (!Array.isArray(columns)) return

  for (const column of columns) {
    const fields = column?.fields
    if (!Array.isArray(fields)) continue
    for (const field of fields) {
      if (!field || typeof field !== 'object') continue
      fn(field)
    }
  }
}

/**
 * Rewrite stale `pipeline_type` literals in a single section's `depends_on` and in every
 * one of its fields (across all columns). Mutates the section in place.
 */
function correctSectionDependencies(section) {
  if (!section || typeof section !== 'object') return

  if (section.depends_on) {
    section.depends_on = correctPipelineTypeCondition(section.depends_on)
  }

  forEachSectionField(section, (field) => {
    if (field.depends_on) {
      field.depends_on = correctPipelineTypeCondition(field.depends_on)
    }
  })
}

/**
 * Correct the pipeline conditions across a parsed side-panel layout in place.
 *
 * Walks every section and every field in each of its columns, rewriting stale
 * `pipeline_type` literals in their `depends_on` (and the section's own `depends_on`).
 * Mutates and returns the passed `sections` array — matching how `getParsedSections`
 * already augments the layout — and tolerates a missing or malformed shape.
 *
 * The correction is applied here, once, as the layout is parsed; `SidePanelLayout.vue`
 * then evaluates the corrected conditions reactively, so switching `pipeline_type` or
 * `status` recomputes visibility without a reload.
 *
 * @param {Array<Object>} [sections]  the parsed side-panel sections
 * @returns {Array<Object>|undefined}  the same array, corrected
 */
export function applyPipelineDependencies(sections) {
  if (!Array.isArray(sections)) return sections

  for (const section of sections) {
    correctSectionDependencies(section)
  }

  return sections
}

/**
 * The Program Type field on CRM Deal (custom_program_type). Registration writes the same
 * fieldname (crm/txb/api/registration.py); TXB-103 fixed its canonical placement on the
 * Delivering Coaching pipeline, which this matrix preserves.
 */
export const PROGRAM_TYPE_FIELDNAME = 'custom_program_type'

/**
 * Semantic section/field group registry (TXB-135 / TXB-170).
 *
 * Each entry names one pipeline-specific piece of the Opportunity layout and describes how
 * to recognise it. Recognition is signature-first: a group is matched by the stable field
 * identifiers it contains (`fieldnames`, `fieldPrefixes`) before falling back to normalized
 * `labelAliases`. That order matters because site-managed layout sections are stored under
 * random `section_<getRandom()>` names, so a section `name` is never a cross-environment
 * authority; its human `label` is the compatibility fallback, and a contained fieldname is
 * the more durable signal when the backend exposes one.
 *
 * `scope`:
 *   - `'section'` — the whole section belongs to the group. When any signature/label
 *     matches, *every* field in the section is gated, which is what collapses the section
 *     (see {@link gateSectionVisibility}).
 *   - `'field'` — only the individually matched field belongs to the group, so its
 *     neighbours in a shared section stay visible (this is how Program Type is hidden
 *     without hiding the section it lives in).
 *
 * `fieldnames`/`fieldPrefixes` are the signature slots. They are populated where the backend
 * fieldname is known (e.g. Program Type's `custom_program_type`) and left open otherwise, so
 * confirming a sheet section's backing fieldname later is a registry edit — not a renderer
 * change. `labelAliases` are normalized on comparison, so casing/whitespace never breaks the
 * fallback.
 */
export const SECTION_GROUPS = {
  SESSIONS: {
    id: 'SESSIONS',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['Sessions'],
  },
  INDIVIDUAL_SESSION_DETAILS: {
    id: 'INDIVIDUAL_SESSION_DETAILS',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['Individual Session Details'],
  },
  BAP_SHEET: {
    id: 'BAP_SHEET',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['BAP Sheet'],
  },
  VCS_SHEET: {
    id: 'VCS_SHEET',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['VCS Sheet'],
  },
  TRAINING_SHEET: {
    id: 'TRAINING_SHEET',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['Training Sheet'],
  },
  DELIVERY_SHEET: {
    id: 'DELIVERY_SHEET',
    scope: 'section',
    fieldnames: [],
    fieldPrefixes: [],
    labelAliases: ['Delivery Sheet'],
  },
  PROGRAM_TYPE: {
    id: 'PROGRAM_TYPE',
    scope: 'field',
    fieldnames: [PROGRAM_TYPE_FIELDNAME],
    fieldPrefixes: [],
    labelAliases: ['Program Type'],
  },
}

/**
 * Pipeline ownership matrix (TXB-135 / TXB-170).
 *
 * The single declarative authority for which Opportunity pipeline owns each semantic group.
 * A group is shown *only* on its owning pipeline; every other pipeline hides it. Groups not
 * listed here (and any section/field that matches no group) are shared and always visible —
 * unknown/unclassified content is kept by default.
 *
 * Changing what a pipeline shows is a matrix edit here, never a renderer condition:
 *   - Individual Session — Sessions, Individual Session Details, BAP Sheet
 *     (backend: BAP belongs to Individual Session).
 *   - Workshop — VCS Sheet (backend: VCS belongs to Workshop).
 *   - Selling Training — Training Sheet.
 *   - Delivering Coaching — Delivery Sheet, Program Type (TXB-103).
 */
export const PIPELINE_OWNERSHIP = {
  [PIPELINE_INDIVIDUAL_SESSION]: [
    SECTION_GROUPS.SESSIONS.id,
    SECTION_GROUPS.INDIVIDUAL_SESSION_DETAILS.id,
    SECTION_GROUPS.BAP_SHEET.id,
  ],
  [PIPELINE_WORKSHOP]: [SECTION_GROUPS.VCS_SHEET.id],
  [PIPELINE_SELLING_TRAINING]: [SECTION_GROUPS.TRAINING_SHEET.id],
  [PIPELINE_DELIVERING_COACHING]: [
    SECTION_GROUPS.DELIVERY_SHEET.id,
    SECTION_GROUPS.PROGRAM_TYPE.id,
  ],
}

/**
 * Declared tab-policy extension point (TXB-170).
 *
 * Shape only, deliberately empty: maps a `pipeline_type` to the activity/Data tab labels it
 * should hide. Wiring exists ({@link resolveVisibleTabs}) so a future tab-ownership decision
 * is a matrix edit, but no current Deal activity, Email, or Calls tab is removed by this
 * correction — Activity Log equivalence and a tab matrix are separately approved work.
 */
export const PIPELINE_TAB_POLICY = {}

/**
 * groupId -> owning pipeline. Derived once from {@link PIPELINE_OWNERSHIP} so the ownership
 * matrix stays the single source of truth (each group has exactly one owner).
 */
const GROUP_OWNER = Object.entries(PIPELINE_OWNERSHIP).reduce(
  (owners, [pipeline, groupIds]) => {
    for (const groupId of groupIds) owners[groupId] = pipeline
    return owners
  },
  {},
)

function normalizeLabel(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
}

/** The `keepVisibleWhen` eval body that keeps a group visible only on its owning pipeline. */
function keepVisibleWhenFor(group) {
  const owner = GROUP_OWNER[group.id]
  return owner ? `doc.pipeline_type == "${owner}"` : ''
}

/** A field carries a group's signature when its fieldname is listed or prefix-matched. */
function fieldMatchesSignature(group, field) {
  const fieldname = field?.fieldname
  if (!fieldname) return false
  if (group.fieldnames?.some((f) => f === fieldname)) return true
  return group.fieldPrefixes?.some((p) => p && fieldname.startsWith(p)) ?? false
}

function labelMatches(group, label) {
  const normalized = normalizeLabel(label)
  if (normalized === '') return false
  return group.labelAliases?.some((l) => normalizeLabel(l) === normalized) ?? false
}

/**
 * Resolve the section-scope group that owns a section: signature-first (any contained field
 * matches a group's fieldnames/prefixes), then the section-label fallback. Returns the group
 * or undefined when the section is shared/unclassified.
 */
function sectionGroupFor(section) {
  const groups = Object.values(SECTION_GROUPS).filter((g) => g.scope === 'section')

  let signatureHit
  forEachSectionField(section, (field) => {
    if (signatureHit) return
    signatureHit = groups.find((g) => fieldMatchesSignature(g, field))
  })
  if (signatureHit) return signatureHit

  return groups.find((g) => labelMatches(g, section?.label))
}

/**
 * Resolve the field-scope group that owns an individual field: signature-first (fieldname),
 * then the field-label fallback. Returns the group or undefined when the field is shared.
 */
function fieldGroupFor(field) {
  const groups = Object.values(SECTION_GROUPS).filter((g) => g.scope === 'field')
  return (
    groups.find((g) => fieldMatchesSignature(g, field)) ??
    groups.find((g) => labelMatches(g, field?.label))
  )
}

/**
 * Reduce a `depends_on` value to a parenthesised eval body, or '' when it imposes no
 * condition. A plain field name (Frappe's non-eval `depends_on`) becomes a truthiness
 * check on that field so it can be composed.
 */
function toEvalBody(dependsOn) {
  if (!dependsOn || typeof dependsOn !== 'string') return ''
  const trimmed = dependsOn.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('eval:')) {
    const body = trimmed.slice(5).trim()
    return body ? `(${body})` : ''
  }
  return `(doc.${trimmed})`
}

/**
 * AND a pipeline visibility condition onto an existing `depends_on`.
 *
 * Preserves the existing condition (so native per-pipeline visibility is not lost) and
 * returns an `eval:` expression. Idempotent: if the exact condition is already present the
 * input is returned unchanged, so repeated resource transforms never nest a duplicate
 * expression. Pure: builds a new string, never mutates input.
 *
 * @param {string} [existing]   the unit's current `depends_on`
 * @param {string} evalBody     the extra condition (eval body, no `eval:` prefix)
 * @returns {string}            the combined `eval:` expression
 */
export function restrictDependsOn(existing, evalBody) {
  if (!evalBody) return existing
  const added = `(${evalBody})`
  if (typeof existing === 'string' && existing.includes(added)) return existing
  const base = toEvalBody(existing)
  return base ? `eval:${base} && ${added}` : `eval:${added}`
}

/**
 * Gate a single section by the ownership matrix, across all of its columns. Mutates the
 * section in place.
 *
 * If the section resolves to a section-scope group, that group's owner condition is ANDed
 * onto *every* field the section holds — this is what hides the whole section, because a
 * section collapses only once all of its fields evaluate hidden. Otherwise each field is
 * gated only when it resolves to a field-scope group (e.g. Program Type), which is how one
 * field is hidden while its neighbours in a shared section stay visible.
 */
function gateSectionVisibility(section) {
  if (!section || typeof section !== 'object') return

  const sectionGroup = sectionGroupFor(section)

  forEachSectionField(section, (field) => {
    const group = sectionGroup ?? fieldGroupFor(field)
    if (!group) return
    field.depends_on = restrictDependsOn(field.depends_on, keepVisibleWhenFor(group))
  })
}

/**
 * Apply the pipeline ownership matrix across a parsed side-panel layout in place.
 *
 * For each section, the same pure resolver ({@link gateSectionVisibility}) that drives the
 * Data tab decides whether the section belongs to a pipeline-owned group and, if so, ANDs
 * the owner condition onto every field — collapsing the section on the non-owning pipelines.
 * Shared/unclassified sections match no group and stay visible.
 *
 * Visibility only — no field is removed and no value is cleared. Mutates and returns the
 * passed array (matching {@link applyPipelineDependencies}) and tolerates a missing or
 * malformed shape.
 *
 * @param {Array<Object>} [sections]  the parsed side-panel sections
 * @returns {Array<Object>|undefined}  the same array, gated by pipeline
 */
export function applyPipelineVisibility(sections) {
  if (!Array.isArray(sections)) return sections

  for (const section of sections) {
    gateSectionVisibility(section)
  }

  return sections
}

/**
 * Apply the declared tab-policy to a parsed tabs array. With the shipped empty
 * {@link PIPELINE_TAB_POLICY} this is a pure no-op that returns the tabs untouched, so no
 * current activity/Email/Calls tab is removed. It exists so a future tab-ownership decision
 * is expressed as policy rather than a renderer condition.
 *
 * @param {Array<Object>} [tabs]         parsed tabs (`{ label|name, sections }`)
 * @param {string} [pipelineType]        the deal's `pipeline_type`
 * @returns {Array<Object>|undefined}    tabs the policy keeps visible
 */
export function resolveVisibleTabs(tabs, pipelineType) {
  if (!Array.isArray(tabs)) return tabs
  const hidden = PIPELINE_TAB_POLICY[pipelineType]
  if (!Array.isArray(hidden) || hidden.length === 0) return tabs
  const hiddenLabels = hidden.map((l) => normalizeLabel(l))
  return tabs.filter(
    (tab) => !hiddenLabels.includes(normalizeLabel(tab?.label ?? tab?.name)),
  )
}

/**
 * Apply the same pipeline authority — stale-literal correction *and* the ownership matrix —
 * to a Data Fields tab layout in place.
 *
 * The Opportunity Data tab (`Activities/DataFields.vue`) loads its own `Data Fields` layout
 * from `get_fields_layout` and hands it straight to `FieldLayout`, which renders the shape
 * `tabs -> sections -> columns -> fields` and evaluates each field's `depends_on` reactively
 * exactly like the side panel. This reuses the one pure resolver ({@link gateSectionVisibility})
 * across every tab's sections (and every column within each section, not just the first) so
 * the same ownership matrix drives both callers. Activity tab *removal* is governed
 * separately by {@link resolveVisibleTabs}; this call leaves the tab list itself untouched.
 *
 * Presentation-only: it never deletes a field or clears a stored value; it only composes
 * visibility constraints onto `depends_on`. Mutates and returns the passed `tabs` array and
 * tolerates a missing or malformed shape.
 *
 * @param {Array<Object>} [tabs]  the parsed Data Fields tabs
 * @returns {Array<Object>|undefined}  the same array, corrected and gated by pipeline
 */
export function applyPipelineTabsLayout(tabs) {
  if (!Array.isArray(tabs)) return tabs

  for (const tab of tabs) {
    if (!tab || typeof tab !== 'object') continue
    const sections = tab.sections
    if (!Array.isArray(sections)) continue

    for (const section of sections) {
      correctSectionDependencies(section)
      gateSectionVisibility(section)
    }
  }

  return tabs
}
