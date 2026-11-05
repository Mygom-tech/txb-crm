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

const FUNNEL_BLUES = [
  '#1f5fa8',
  '#2470bf',
  '#2d87d6',
  '#4393da',
  '#589fdf',
  '#6dace3',
]
const FUNNEL_MINE = '#7c3aed'

// ECharts options for the referrals leaderboard drawn as a funnel: one bar per rep, widest at
// the top, width in proportion to points. The viewer's bar stands out.
export function referralFunnelOptions(
  reps = [],
  { me, nameOf = (u) => u } = {},
) {
  const max = Math.max(1, ...reps.map((rep) => rep.points))
  return {
    animationDuration: 500,
    textStyle: { fontFamily: ['InterVar', 'sans-serif'] },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (p) => `${p.name}: +${p.value}`,
    },
    series: [
      {
        type: 'funnel',
        sort: 'none',
        left: '5%',
        width: '90%',
        top: 8,
        bottom: 8,
        min: 0,
        max,
        minSize: '35%',
        gap: 4,
        label: {
          position: 'inside',
          color: '#fff',
          fontSize: 13,
          formatter: (p) => `${p.name}  +${p.value}`,
        },
        data: reps.map((rep, i) => ({
          name: nameOf(rep.user),
          value: rep.points,
          itemStyle: {
            color:
              rep.user === me
                ? FUNNEL_MINE
                : FUNNEL_BLUES[Math.min(i, FUNNEL_BLUES.length - 1)],
          },
          label: { fontWeight: rep.user === me ? 'bold' : 'normal' },
        })),
      },
    ],
  }
}
