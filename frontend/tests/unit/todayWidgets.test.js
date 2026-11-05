import { describe, it, expect } from 'vitest'
import {
  groupTasks,
  bandReps,
  referralFunnelOptions,
} from '@/utils/todayWidgets'

describe('groupTasks', () => {
  it('groups in Overdue, Today, Completed order and drops empty groups', () => {
    const groups = groupTasks([
      { name: 'a', group: 'today' },
      { name: 'b', group: 'overdue' },
      { name: 'c', group: 'today' },
    ])
    expect(groups.map((g) => [g.key, g.count])).toEqual([
      ['overdue', 1],
      ['today', 2],
    ])
    expect(groups[1].items.map((t) => t.name)).toEqual(['a', 'c'])
  })
})

describe('bandReps', () => {
  const reps = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((user, i) => ({
    user,
    points: 7 - i,
  }))

  it('shows the top five and counts the rest when the viewer is among them', () => {
    const { shown, more } = bandReps(reps, 'b')
    expect(shown.map((r) => r.user)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(more).toBe(2)
  })

  it('adds the viewer with their rank when outside the top five', () => {
    const { shown, more } = bandReps(reps, 'g')
    expect(shown.map((r) => [r.user, r.rank])).toEqual([
      ['a', 1],
      ['b', 2],
      ['c', 3],
      ['d', 4],
      ['e', 5],
      ['g', 7],
    ])
    expect(more).toBe(1)
  })

  it('shows everyone when there are fewer than five', () => {
    expect(bandReps(reps.slice(0, 2), 'z')).toEqual({
      shown: [
        { user: 'a', points: 7, rank: 1 },
        { user: 'b', points: 6, rank: 2 },
      ],
      more: 0,
    })
  })
})

describe('referralFunnelOptions', () => {
  it('keeps the leaderboard order, scales to the top score and marks the viewer', () => {
    const reps = [
      { user: 'a', points: 4 },
      { user: 'me', points: 2 },
    ]
    const [series] = referralFunnelOptions(reps, {
      me: 'me',
      nameOf: (u) => u.toUpperCase(),
    }).series
    expect(series.sort).toBe('none')
    expect(series.max).toBe(4)
    expect(series.data.map((d) => [d.name, d.value])).toEqual([
      ['A', 4],
      ['ME', 2],
    ])
    expect(series.data[1].itemStyle.color).not.toBe(
      series.data[0].itemStyle.color,
    )
    expect(series.data[1].label.fontWeight).toBe('bold')
  })
})
