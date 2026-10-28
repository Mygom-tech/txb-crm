// Dashboard Today widgets (specs/dashboard-today-widgets.md).

export const TASK_GROUPS = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'done', label: 'Completed today' },
]

// The non-empty task groups, in display order, from crm.api.dashboard.get_today_tasks rows.
export function groupTasks(tasks = []) {
  return TASK_GROUPS.map((group) => {
    const items = tasks.filter((t) => t.group === group.key)
    return { ...group, items, count: items.length }
  }).filter((group) => group.count)
}

// The referrals band: the top `limit` reps, plus the viewer when they score but are not among
// them, and how many reps are left for the "+N more" chip. Each rep carries its rank.
export function bandReps(reps = [], me, limit = 5) {
  const ranked = reps.map((rep, i) => ({ ...rep, rank: i + 1 }))
  const shown = ranked.slice(0, limit)
  const mine = ranked.find((rep) => rep.user === me)
  if (mine && mine.rank > limit) shown.push(mine)
  return { shown, more: ranked.length - shown.length }
}
