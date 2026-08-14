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
 * Correct the pipeline conditions across a parsed side-panel layout in place.
 *
 * Walks every section and every field in its first column, rewriting stale `pipeline_type`
 * literals in their `depends_on` (and the section's own `depends_on`). Mutates and returns
 * the passed `sections` array — matching how `getParsedSections` already augments the
 * layout — and tolerates a missing or malformed shape.
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
    if (!section || typeof section !== 'object') continue

    if (section.depends_on) {
      section.depends_on = correctPipelineTypeCondition(section.depends_on)
    }

    const fields = section.columns?.[0]?.fields
    if (!Array.isArray(fields)) continue

    for (const field of fields) {
      if (!field || typeof field !== 'object') continue
      if (field.depends_on) {
        field.depends_on = correctPipelineTypeCondition(field.depends_on)
      }
    }
  }

  return sections
}
