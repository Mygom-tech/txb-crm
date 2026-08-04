/**
 * Which statuses a deal may move to, and which action makes each move.
 *
 * The graph comes from the backend (crm.txb.api.transitions.get_transition_map), which
 * derives it from the same action registry that execute_action enforces. These helpers
 * are pure so they can be tested without a browser or a Frappe site.
 *
 * Nothing here is a security boundary — guard_transition is.
 */

/**
 * The edges available from `from`.
 *
 * A status outside its pipeline's own list has no graph row — real data contains one, a
 * Workshop sitting at "Active". Actions declaring no `from_states` still apply from
 * there, and the server's `is_allowed` says so, so the `"*"` row stands in. A status
 * WITH its own row already includes those actions (the graph expands empty `from_states`
 * across the pipeline's statuses), so the rows are alternatives, never merged.
 */
function edgesFrom(transitions, pipeline, from) {
  const graph = transitions?.[pipeline] || {}
  return graph[from] || graph['*'] || {}
}

/**
 * Statuses reachable from `from` in this pipeline.
 *
 * @param {Object} transitions  {pipeline: {from: {to: [{name,label}]}}}
 * @returns {string[]}
 */
export function allowedTargets(transitions, pipeline, from) {
  return Object.keys(edgesFrom(transitions, pipeline, from))
}

/**
 * The actions that make this move AND that the server is currently offering.
 *
 * Intersecting with `available` matters: the graph is static, but
 * get_available_actions has already filtered by role, so an edge whose only action is
 * admin-only disappears for a coach instead of failing at submit time.
 *
 * @param {Array} available - from crm.txb.api.actions.get_available_actions
 * @returns {Array} the full action objects, in graph order
 */
export function candidateActions(transitions, pipeline, from, to, available) {
  const names = (edgesFrom(transitions, pipeline, from)[to] || []).map(
    (a) => a.name,
  )
  const byName = new Map((available || []).map((a) => [a.name, a]))

  return names
    .filter((name) => byName.has(name))
    .map((name) => byName.get(name))
}

/**
 * Branch values that land the deal in `to`, so dropping on a column pre-selects the
 * answer that column implies (TXB-110 decision 3).
 *
 * When more than one value reaches the same target nothing is pre-filled — `run_bap`
 * reaches "Session Run" from both "Follow-up needed" and "Not interested", and picking
 * one silently is the guessing this design rejected.
 *
 * @returns {Object} {fieldname: value}, possibly empty
 */
export function prefillFor(action, to) {
  const prefill = {}

  for (const [fieldname, targets] of Object.entries(
    action?.to_state_map || {},
  )) {
    const values = Object.entries(targets)
      .filter(([, target]) => target === to)
      .map(([value]) => value)

    if (values.length === 1) prefill[fieldname] = values[0]
  }

  return prefill
}

/**
 * Whether a card dragged from `from` may be dropped on `to`.
 *
 * A user who may not change the status at all (a coach on Delivering Coaching, per
 * TXB-105) is refused every column.
 */
export function canDropOn(transitions, pipeline, from, to, canChangeStatus) {
  if (!canChangeStatus) return false
  if (from === to) return true

  return allowedTargets(transitions, pipeline, from).includes(to)
}
