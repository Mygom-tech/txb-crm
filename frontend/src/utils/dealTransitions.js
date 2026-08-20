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
 * How a status change resolves, decided by the transition registry — never by whatever the
 * available-actions response happened to contain.
 *
 * Ownership is the graph's call. An edge the graph records as action-owned MUST run that
 * action or fail closed; it can never fall through to a bare status write, however empty,
 * stale, malformed or unmatched the available-action metadata is. Only an edge the graph
 * does not describe is a candidate for the Admin recovery hatch. Collapsing "no owning
 * action" and "no available match" into one empty list is exactly what let Won/Sold be
 * written bare (TXB-175), so the two are kept distinct here.
 */
export const STATUS_CHANGE_ACTION = 'action' // an owning action is available — run it
export const STATUS_CHANGE_BLOCKED = 'blocked' // the graph owns this edge but no available action matches — refuse
export const STATUS_CHANGE_UNOWNED = 'unowned' // no action owns this edge — Admin may write it bare, others are refused

/**
 * Whether any registered action owns the `from → to` edge in the transition graph.
 */
export function edgeIsActionOwned(transitions, pipeline, from, to) {
  return (edgesFrom(transitions, pipeline, from)[to] || []).length > 0
}

/**
 * Resolve a status change against a set of available actions, discriminating the graph's
 * three outcomes (see the STATUS_CHANGE_* constants) instead of returning a bare candidate
 * list. A graph-owned edge with no matching available action resolves to BLOCKED, never to
 * the empty list a caller would read as "no action owns this, write it directly".
 *
 * @returns {{kind: string, candidates?: Array}}
 */
export function resolveStatusChange(transitions, pipeline, from, to, available) {
  if (!edgeIsActionOwned(transitions, pipeline, from, to)) {
    return { kind: STATUS_CHANGE_UNOWNED }
  }

  const candidates = candidateActions(transitions, pipeline, from, to, available)

  if (!candidates.length) {
    return { kind: STATUS_CHANGE_BLOCKED }
  }

  return { kind: STATUS_CHANGE_ACTION, candidates }
}

/**
 * Resolve an action-owned status move against a FRESH server response.
 *
 * Available actions are filtered by the deal's current status. A cached response from the
 * previous status can therefore make a real graph edge look actionless and send Admins down
 * the bare-write recovery path. The caller supplies an uncached loader so a status selection
 * never decides whether to bypass an action from stale or initial-empty data.
 *
 * When the graph does not own the edge there is no action to look up, so the loader is not
 * called: the move is the Admin recovery hatch (or a refusal for everyone else) and resolves
 * to UNOWNED. When the graph DOES own it, the loader must resolve to a real
 * get_available_actions response, which always carries an `actions` array — empty only when
 * the server genuinely offers nothing here. A null body, an error object or any other
 * malformed-but-resolved payload is "we could not find out", not "this edge has no action";
 * coercing it to [] would look actionless and silently drop an Admin onto the bare-write
 * path, skipping a modal that in fact owns the edge. So a rejected loader AND a malformed
 * success both throw, and the caller aborts the change rather than bypassing the action.
 *
 * @returns {Promise<{kind: string, candidates?: Array}>}
 */
export async function refreshStatusResolution({
  transitions,
  pipeline,
  from,
  to,
  loadAvailable,
}) {
  if (!edgeIsActionOwned(transitions, pipeline, from, to)) {
    return { kind: STATUS_CHANGE_UNOWNED }
  }

  const response = await loadAvailable()

  if (!Array.isArray(response?.actions)) {
    throw new Error('get_available_actions returned no actions array')
  }

  return resolveStatusChange(transitions, pipeline, from, to, response.actions)
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
 *
 * `adminStatuses`, when given and non-empty, is the pipeline's full status list and means
 * the user holds the recovery hatch: they may move the deal anywhere within its own
 * pipeline. An empty list means "not known yet" — `allowedStatusesFor` returns `[]` both
 * before its resource loads and for an unmapped pipeline — so it falls through to the
 * graph rules rather than refusing everything, which would grey the board for the very
 * user the hatch exists for. The action modal still opens when an action owns the edge,
 * so nothing an action records is skipped — the hatch widens where they may go, not what
 * gets captured.
 */
export function canDropOn(
  transitions,
  pipeline,
  from,
  to,
  canChangeStatus,
  adminStatuses = null,
) {
  if (!canChangeStatus) return false
  if (from === to) return true
  if (adminStatuses?.length) return adminStatuses.includes(to)

  return allowedTargets(transitions, pipeline, from).includes(to)
}
