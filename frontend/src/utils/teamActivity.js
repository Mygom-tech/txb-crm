// Team Activity (TXB-287): pure helpers behind pages/TeamActivity.vue. The page renders whatever
// metrics `crm.txb.api.team_activity.summary` returns, so nothing here knows a metric key.

import { taskReference } from '@/components/ContactInactivity/taskReference'

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

// Calendar-date arithmetic on 'YYYY-MM-DD' strings, done in UTC so no timezone shifts the day.
function addDays(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

/** The inclusive range of the last `days` calendar days, today included, in `timezone`. */
export function lastDays(days, timezone, now = new Date()) {
  const to = todayIn(timezone, now)
  return { from: addDays(to, -(days - 1)), to }
}

/** The Monday–Sunday week holding today, or `weeksAgo` weeks before it, in `timezone`. */
export function weekRange(timezone, weeksAgo = 0, now = new Date()) {
  const today = todayIn(timezone, now)
  const weekday = (new Date(`${today}T00:00:00Z`).getUTCDay() + 6) % 7
  const from = addDays(today, -weekday - 7 * weeksAgo)
  return { from, to: addDays(from, 6) }
}

/** The calendar month holding today, in `timezone`. */
export function monthRange(timezone, now = new Date()) {
  const from = `${todayIn(timezone, now).slice(0, 8)}01`
  const next = new Date(`${from}T00:00:00Z`)
  next.setUTCMonth(next.getUTCMonth() + 1)
  return { from, to: addDays(next.toISOString().slice(0, 10), -1) }
}

// Period presets, by their English key (the label is translated where shown). The first is the
// page default: Monday to Sunday, so a week's figures do not slide every day.
export const PERIOD_PRESETS = {
  'This Week': (timezone) => weekRange(timezone),
  'Last Week': (timezone) => weekRange(timezone, 1),
  'This Month': (timezone) => monthRange(timezone),
  'Last 30 Days': (timezone) => lastDays(30, timezone),
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

/** records() params: the same period and member as the summary, plus metric, page and group. */
export function recordsParams(metric, filters, page = 1, group = null) {
  const params = { metric, ...summaryParams(filters), page }
  if (group) params.group = group
  return params
}

/**
 * Records rows with a stable ListView key (rows carry no common unique field) and the page of
 * the Lead, Deal or Contact they reference, if any.
 */
export function recordRows(rows = [], page = 1) {
  return rows.map((row, index) => ({
    ...row,
    _key: `${page}:${index}`,
    _reference: taskReference(row.reference_doctype, row.reference_name),
  }))
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
