import { describe, it, expect } from 'vitest'
import { contactTabCounts } from '@/utils/contactActivity'

describe('contactTabCounts', () => {
  it('counts each category of the Contact feed', () => {
    const feed = {
      versions: [
        { activity_type: 'communication' },
        { activity_type: 'communication' },
        { activity_type: 'comment' },
        { activity_type: 'event', data: { meeting_action: 'scheduled' } },
        { activity_type: 'event', data: { meeting_action: 'rescheduled' } },
        { activity_type: 'creation' },
      ],
      calls: [{}],
      notes: [{}, {}, {}],
      tasks: [{}, {}],
    }
    expect(contactTabCounts(feed)).toEqual({
      Emails: 2,
      Comments: 1,
      Events: 1,
      Calls: 1,
      Tasks: 2,
      Notes: 3,
    })
  })

  it('is all zeros before the feed loads', () => {
    expect(contactTabCounts(undefined)).toEqual({
      Emails: 0,
      Comments: 0,
      Events: 0,
      Calls: 0,
      Tasks: 0,
      Notes: 0,
    })
  })
})
