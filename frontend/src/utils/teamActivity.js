// Team Activity (TXB-287): pure helpers behind pages/TeamActivity.vue. The page renders whatever
// metrics `crm.txb.api.team_activity.summary` returns, so nothing here knows a metric key.

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** Today's calendar date ('YYYY-MM-DD') in `timezone` (the site timezone). */
export function todayIn(timezone, now = new Date()) {
  // en-CA formats as YYYY-MM-DD.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone || undefined,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
}

/** The inclusive range of the last `days` calendar days, today included, in `timezone`. */
export function lastDays(days, timezone, now = new Date()) {
  const to = todayIn(timezone, now)
  const from = new Date(`${to}T00:00:00Z`)
  from.setUTCDate(from.getUTCDate() - (days - 1))
  return { from: from.toISOString().slice(0, 10), to }
}

/** Why a from/to pair can't be requested, or null when it can. */
export function rangeError(from, to) {
  if (!from || !to) return __('Choose a start and an end date.')
  if (!ISO_DATE.test(from) || !ISO_DATE.test(to)) {
    return __('Dates must be in YYYY-MM-DD format.')
  }
  if (from > to) return __('The start date must not be after the end date.')
  return null
}

/** summary() params; the whole team sends no member at all. */
export function summaryParams({ from, to, member }) {
  const params = { from_date: from, to_date: to }
  if (member) params.member = member
  return params
}

/** records() params: the same period and member as the summary, plus metric and page. */
export function recordsParams(metric, filters, page = 1) {
  return { metric, ...summaryParams(filters), page }
}

/**
 * ListView columns from a metric's `record_columns`. A per-pair `activity_count` column renders
 * as an "N activities" badge (type 'repeat'), shown only when the pair has more than one.
 */
export function recordsColumns(recordColumns = []) {
  return recordColumns.map((column) => ({
    key: column.key,
    label: column.label,
    type: column.key === 'activity_count' ? 'repeat' : column.type,
  }))
}

/** The repeat count to badge for a row, or null when it would be 1 or less. */
export function repeatCount(row) {
  const count = Number(row?.activity_count)
  return count > 1 ? count : null
}

/** Records rows with a stable ListView key: rows carry no common unique field. */
export function recordRows(rows = [], page = 1) {
  return rows.map((row, index) => ({ ...row, _key: `${page}:${index}` }))
}

/** The "{from}–{to} of {total}" window of a records page and whether a next page exists. */
export function pageWindow(page, pageLength, total) {
  if (!total) return { from: 0, to: 0, hasNext: false }
  const from = (page - 1) * pageLength + 1
  const to = Math.min(page * pageLength, total)
  return { from, to, hasNext: to < total }
}

/** Select options from members(): a list of users, or `{members: [...]}` keyed by `user`. */
export function memberOptions(response) {
  const members = Array.isArray(response) ? response : response?.members || []
  return members.map((member) => {
    const value = member.name || member.user
    return { label: member.full_name || value, value }
  })
}

/** True when every metric is 0 (and there is at least one). */
export function allZero(metrics = []) {
  return metrics.length > 0 && metrics.every((metric) => !metric.value)
}
