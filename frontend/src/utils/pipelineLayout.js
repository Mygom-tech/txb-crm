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
 * Pipeline presentation matrix (TXB-135).
 *
 * Each Opportunity pipeline shows only the fields and sections approved for it. Rather than
 * delete anything, we tighten the relevant *fields'* `depends_on` so `SidePanelLayout.vue`
 * evaluates it reactively and simply stops rendering them for the excluded pipelines — the
 * stored values stay on the document untouched, and switching `pipeline_type` re-shows them
 * with no reload.
 *
 * Field-level gating is the only lever that works here: `SidePanelLayout.parsedSection`
 * derives a section's visibility purely from its visible-field count
 * (`section.visible = isContactSection || columns[0].fields.filter(f => f.visible).length`)
 * and never evaluates `section.depends_on`. So a *section* rule is enforced by gating every
 * field the section contains — once all its fields evaluate hidden the section collapses.
 *
 * `keepVisibleWhen` is an eval expression body (no `eval:` prefix) that must hold for a
 * field to remain visible; it is ANDed onto that field's existing `depends_on` (see
 * {@link restrictDependsOn}), so a pipeline's native visibility for fields this matrix does
 * not name is left exactly as the layout defines it. A rule matches a *section* (whose every
 * field it then gates) by the section `label`, and an individual *field* by its `fieldname`
 * or `label`.
 *
 *   - Individual Session Details / Sessions — whole sections hidden on Workshop only.
 *   - Program Type — kept only on Delivering Coaching (TXB-103); Workshop, Individual
 *     Session and Selling Training all hide it.
 */
export const PIPELINE_VISIBILITY_RULES = [
  {
    labels: ['Individual Session Details', 'Sessions'],
    keepVisibleWhen: `doc.pipeline_type != "${PIPELINE_WORKSHOP}"`,
  },
  {
    labels: ['Program Type'],
    fieldnames: [PROGRAM_TYPE_FIELDNAME],
    keepVisibleWhen: `doc.pipeline_type == "${PIPELINE_DELIVERING_COACHING}"`,
  },
]

function normalizeLabel(value) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
}

function ruleMatchesSection(rule, section) {
  if (!rule.labels) return false
  const label = normalizeLabel(section.label)
  return label !== '' && rule.labels.some((l) => normalizeLabel(l) === label)
}

function ruleMatchesField(rule, field) {
  if (rule.fieldnames?.some((f) => f === field.fieldname)) return true
  if (!rule.labels) return false
  const label = normalizeLabel(field.label)
  return label !== '' && rule.labels.some((l) => normalizeLabel(l) === label)
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
 * returns an `eval:` expression. Pure: builds a new string, never mutates input.
 *
 * @param {string} [existing]   the unit's current `depends_on`
 * @param {string} evalBody     the extra condition (eval body, no `eval:` prefix)
 * @returns {string}            the combined `eval:` expression
 */
export function restrictDependsOn(existing, evalBody) {
  const added = `(${evalBody})`
  const base = toEvalBody(existing)
  return base ? `eval:${base} && ${added}` : `eval:${added}`
}

/**
 * Gate a single section by the presentation matrix, across all of its columns. Mutates the
 * section in place.
 *
 * A section-level rule match (by `label`) ANDs its `keepVisibleWhen` onto *every* field the
 * section holds — this is what hides the whole section, because a section collapses only
 * once all of its fields evaluate hidden. Otherwise a field is gated only when a rule names
 * it directly (by `fieldname`/`label`), which is how the Program Type field is hidden while
 * its neighbours in a shared section stay visible.
 */
function gateSectionVisibility(section) {
  if (!section || typeof section !== 'object') return

  const sectionRule = PIPELINE_VISIBILITY_RULES.find((rule) =>
    ruleMatchesSection(rule, section),
  )

  forEachSectionField(section, (field) => {
    const rule =
      sectionRule ??
      PIPELINE_VISIBILITY_RULES.find((r) => ruleMatchesField(r, field))
    if (rule) {
      field.depends_on = restrictDependsOn(field.depends_on, rule.keepVisibleWhen)
    }
  })
}

/**
 * Apply the pipeline presentation matrix across a parsed side-panel layout in place.
 *
 * For each section, if a {@link PIPELINE_VISIBILITY_RULES} rule matches the section (by
 * label) its `keepVisibleWhen` is ANDed onto *every* field in the section — this is what
 * actually hides the section, because a section collapses only once all of its fields are
 * hidden. Otherwise each field is gated individually when a rule matches it (by
 * `fieldname`/`label`), which is how the Program Type field is hidden while its neighbours
 * in a shared section stay visible.
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
 * Apply the same pipeline authority — stale-literal correction *and* the TXB-135
 * presentation matrix — to a Data Fields tab layout in place.
 *
 * The Opportunity Data tab (`Activities/DataFields.vue`) loads its own `Data Fields` layout
 * from `get_fields_layout` and hands it straight to `FieldLayout`, which renders the shape
 * `tabs -> sections -> columns -> fields` and evaluates each field's `depends_on` reactively
 * exactly like the side panel. PR #31 gated only Deal.vue's side-panel sections, so a
 * Workshop deal still rendered Sessions and Individual Session Details on the Data tab.
 * This walks every tab's sections (and every column within each section, not just the
 * first) so the one shared matrix drives both callers.
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
