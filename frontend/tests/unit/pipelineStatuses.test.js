import { describe, it, expect } from 'vitest'
import {
  allowedStatusesFor,
  statusLinkFilters,
  conversionPipelineTypes,
  conversionInitialStatus,
} from '@/utils/pipelineStatuses'
import {
  PENDING_REVIEW,
  isDisqualifiedReasonUnresolved,
  reasonAfterSkip,
  initialRouteTab,
} from '@/utils/leadReasonPrompt'
import {
  COACHING_PIPELINE_TYPE,
  DEAL_DOCTYPE,
  isCoachingPipeline,
  notesTabLabel,
  hideCallDuration,
} from '@/utils/dealPresentation'
import {
  PIPELINE_INDIVIDUAL_SESSION,
  PIPELINE_WORKSHOP,
  PIPELINE_SELLING_TRAINING,
  PIPELINE_DELIVERING_COACHING,
  STALE_PIPELINE_TYPE_ALIASES,
  correctPipelineTypeCondition,
  applyPipelineDependencies,
} from '@/utils/pipelineLayout'

const MAP = {
  'Individual Session': [
    'Submitted',
    'Session Set',
    'Session Run',
    'Won',
    'Lost',
  ],
  Workshop: [
    'Workshop submitted',
    'VCS call set',
    'Workshop set',
    'Sold',
    'Lost',
  ],
  'Delivering Coaching': [
    'Submitted',
    'Waiting on Review',
    'Active',
    'Inactive',
  ],
}

describe('allowedStatusesFor', () => {
  it('returns only the pipeline’s statuses', () => {
    expect(allowedStatusesFor('Workshop', 'Workshop set', MAP)).toEqual([
      'Workshop submitted',
      'VCS call set',
      'Workshop set',
      'Sold',
      'Lost',
    ])
  })

  it('does not leak statuses from other pipelines', () => {
    const allowed = allowedStatusesFor('Workshop', 'Workshop set', MAP)
    expect(allowed).not.toContain('Session Run')
    expect(allowed).not.toContain('Waiting on Review')
  })

  it('preserves the declared display order', () => {
    expect(allowedStatusesFor('Delivering Coaching', 'Active', MAP)).toEqual([
      'Submitted',
      'Waiting on Review',
      'Active',
      'Inactive',
    ])
  })

  it('keeps a status shared by two pipelines in both', () => {
    // "Lost" and "Submitted" are genuinely many-to-many.
    expect(allowedStatusesFor('Individual Session', 'Lost', MAP)).toContain(
      'Lost',
    )
    expect(allowedStatusesFor('Workshop', 'Lost', MAP)).toContain('Lost')
    expect(
      allowedStatusesFor('Delivering Coaching', 'Submitted', MAP),
    ).toContain('Submitted')
  })

  it('always includes the current status even when off-map', () => {
    // Real data: a Workshop deal sitting in "Active", a Delivering Coaching status.
    // Dropping it would leave that deal unable to display or change its status.
    const allowed = allowedStatusesFor('Workshop', 'Active', MAP)
    expect(allowed).toContain('Active')
    expect(allowed[allowed.length - 1]).toBe('Active')
  })

  it('does not duplicate a current status already in the list', () => {
    const allowed = allowedStatusesFor('Workshop', 'Sold', MAP)
    expect(allowed.filter((s) => s === 'Sold')).toHaveLength(1)
  })

  it('falls back to no filtering for an unknown pipeline', () => {
    expect(allowedStatusesFor('Nonexistent', 'Won', MAP)).toEqual([])
    expect(allowedStatusesFor(undefined, 'Won', MAP)).toEqual([])
    expect(allowedStatusesFor('Workshop', 'Sold', undefined)).toEqual([])
    expect(allowedStatusesFor('Workshop', 'Sold', {})).toEqual([])
  })

  it('handles a missing current status', () => {
    expect(allowedStatusesFor('Workshop', null, MAP)).toEqual(MAP.Workshop)
  })

  it('never mutates the source map', () => {
    const before = [...MAP.Workshop]
    allowedStatusesFor('Workshop', 'Active', MAP)
    expect(MAP.Workshop).toEqual(before)
  })
})

describe('conversion pipeline restriction (TXB-125)', () => {
  it('offers only the three approved pipelines, in order', () => {
    expect(conversionPipelineTypes()).toEqual([
      'Individual Session',
      'Workshop',
      'Selling Training',
    ])
  })

  it('does not offer any other pipeline for conversion', () => {
    expect(conversionPipelineTypes()).not.toContain('Delivering Coaching')
  })

  it('maps each approved pipeline to its required initial state', () => {
    expect(conversionInitialStatus('Individual Session')).toBe('Submitted')
    expect(conversionInitialStatus('Workshop')).toBe('Workshop submitted')
    expect(conversionInitialStatus('Selling Training')).toBe(
      'Training submitted',
    )
  })

  it('returns no initial state for a non-convertible pipeline', () => {
    expect(conversionInitialStatus('Delivering Coaching')).toBe('')
    expect(conversionInitialStatus(undefined)).toBe('')
    expect(conversionInitialStatus('')).toBe('')
  })
})

describe('statusLinkFilters', () => {
  it('builds an "in" filter for the link field', () => {
    expect(statusLinkFilters('Workshop', 'Sold', MAP)).toEqual({
      name: [
        'in',
        ['Workshop submitted', 'VCS call set', 'Workshop set', 'Sold', 'Lost'],
      ],
    })
  })

  it('includes an off-map current status so the field can render it', () => {
    expect(statusLinkFilters('Workshop', 'Active', MAP).name[1]).toContain(
      'Active',
    )
  })

  it('returns null when there is nothing to restrict', () => {
    expect(statusLinkFilters('Nonexistent', 'Won', MAP)).toBeNull()
    expect(statusLinkFilters('Workshop', 'Sold', {})).toBeNull()
  })
})

// Lead reason prompting + Data-tab default (TXB-146): the native replacements for the
// `Disqualified Reason Prompt` and `Lead Creation Redirect` runtime Form Scripts. The
// decision logic lives in @/utils/leadReasonPrompt and is exercised here as pure functions.
const LOST_STATUSES = new Set(['Disqualified', 'Lost', 'Closed'])
const getLeadStatus = (status) => ({
  type: LOST_STATUSES.has(status) ? 'Lost' : 'Open',
})

describe('isDisqualifiedReasonUnresolved (Disqualified Reason Prompt port)', () => {
  it('re-prompts a Disqualified lead whose reason is blank', () => {
    expect(
      isDisqualifiedReasonUnresolved(
        { status: 'Disqualified', lost_reason: '' },
        getLeadStatus,
      ),
    ).toBe(true)
    expect(
      isDisqualifiedReasonUnresolved(
        { status: 'Disqualified', lost_reason: '   ' },
        getLeadStatus,
      ),
    ).toBe(true)
  })

  it('re-prompts when the reason is the Pending Review placeholder', () => {
    expect(
      isDisqualifiedReasonUnresolved(
        { status: 'Disqualified', lost_reason: PENDING_REVIEW },
        getLeadStatus,
      ),
    ).toBe(true)
  })

  it('does not auto-open once a real reason is chosen (resolved)', () => {
    expect(
      isDisqualifiedReasonUnresolved(
        { status: 'Disqualified', lost_reason: 'Budget' },
        getLeadStatus,
      ),
    ).toBe(false)
  })

  it('never prompts for non-Lost statuses even with a blank reason', () => {
    expect(
      isDisqualifiedReasonUnresolved(
        { status: 'Open', lost_reason: '' },
        getLeadStatus,
      ),
    ).toBe(false)
  })

  it('is safe with a missing doc, status, or resolver', () => {
    expect(isDisqualifiedReasonUnresolved(null, getLeadStatus)).toBe(false)
    expect(isDisqualifiedReasonUnresolved({}, getLeadStatus)).toBe(false)
    expect(
      isDisqualifiedReasonUnresolved({ status: 'Disqualified' }, null),
    ).toBe(false)
  })
})

describe('reasonAfterSkip (skippable Pending Review behavior)', () => {
  it('turns a blank/absent reason into Pending Review', () => {
    expect(reasonAfterSkip('')).toBe(PENDING_REVIEW)
    expect(reasonAfterSkip('   ')).toBe(PENDING_REVIEW)
    expect(reasonAfterSkip(undefined)).toBe(PENDING_REVIEW)
  })

  it('keeps Pending Review as Pending Review so it re-prompts next open', () => {
    expect(reasonAfterSkip(PENDING_REVIEW)).toBe(PENDING_REVIEW)
  })

  it('preserves an already chosen real reason', () => {
    expect(reasonAfterSkip('Budget')).toBe('Budget')
  })
})

// initialRouteTab is invoked by the router guard only for a Lead/Deal route that arrives
// without a hash; the guard's `!to.hash` check is what preserves an explicit in-session tab
// selection (an explicit selection always carries a hash and never reaches this branch).
describe('initialRouteTab (Lead Creation Redirect port)', () => {
  it('redirects a freshly opened lead route to the Data tab', () => {
    expect(initialRouteTab('Lead', 'activity')).toBe('data')
    // Even a stored last-visited tab does not divert a newly opened lead off Data.
    expect(initialRouteTab('Lead', 'emails')).toBe('data')
    expect(initialRouteTab('Lead', undefined)).toBe('data')
  })

  it('leaves deals resuming their last-visited tab', () => {
    expect(initialRouteTab('Deal', 'notes')).toBe('notes')
    expect(initialRouteTab('Deal', '')).toBe('activity')
    expect(initialRouteTab('Deal', undefined)).toBe('activity')
  })
})

// Regression coverage for the code-owned replacements of the `Notes Tab Rename` and
// `Hide Call Duration` Form Scripts. Both used to mutate the DOM after render; these pure
// helpers now drive the same behaviour and must keep it scoped exactly as before.
describe('isCoachingPipeline', () => {
  it('is true only for the Delivering Coaching pipeline', () => {
    expect(isCoachingPipeline(COACHING_PIPELINE_TYPE)).toBe(true)
    expect(isCoachingPipeline('Delivering Coaching')).toBe(true)
  })

  it('is false for every other pipeline and for missing values', () => {
    expect(isCoachingPipeline('Individual Session')).toBe(false)
    expect(isCoachingPipeline('Workshop')).toBe(false)
    expect(isCoachingPipeline('')).toBe(false)
    expect(isCoachingPipeline(undefined)).toBe(false)
  })
})

describe('notesTabLabel', () => {
  it('labels the Notes tab "Coaching Notes" for coaching deals', () => {
    expect(notesTabLabel('Delivering Coaching')).toBe('Coaching Notes')
  })

  it('keeps "Notes" for every other pipeline', () => {
    expect(notesTabLabel('Individual Session')).toBe('Notes')
    expect(notesTabLabel('Workshop')).toBe('Notes')
    expect(notesTabLabel('Selling Training')).toBe('Notes')
  })

  it('keeps "Notes" when the pipeline is unknown or unset', () => {
    expect(notesTabLabel('')).toBe('Notes')
    expect(notesTabLabel(undefined)).toBe('Notes')
  })
})

describe('hideCallDuration', () => {
  it('hides the duration badge only on the Deal entity', () => {
    expect(hideCallDuration(DEAL_DOCTYPE)).toBe(true)
    expect(hideCallDuration('CRM Deal')).toBe(true)
  })

  it('keeps the duration badge on every other entity page', () => {
    expect(hideCallDuration('CRM Lead')).toBe(false)
    expect(hideCallDuration('Contact')).toBe(false)
    expect(hideCallDuration('')).toBe(false)
    expect(hideCallDuration(undefined)).toBe(false)
  })
})

// TXB-148: the Pipeline Section Visibility Form Script is replaced by committed pipeline
// depends_on (pipelineLayout.js), applied in Deal.vue getParsedSections and evaluated
// reactively by SidePanelLayout. These cover the correction of its stale "Training"
// condition so Selling Training deals resolve their pipeline-specific fields/sections.
describe('pipeline type constants', () => {
  it('name the four TXB pipelines exactly as the backend does', () => {
    // Mirrors crm/txb/constants.py; "Selling Training" is the value the retired Form
    // Script got wrong ("Training").
    expect(PIPELINE_INDIVIDUAL_SESSION).toBe('Individual Session')
    expect(PIPELINE_WORKSHOP).toBe('Workshop')
    expect(PIPELINE_SELLING_TRAINING).toBe('Selling Training')
    expect(PIPELINE_DELIVERING_COACHING).toBe('Delivering Coaching')
  })

  it('records the stale "Training" alias and its correction', () => {
    expect(STALE_PIPELINE_TYPE_ALIASES.Training).toBe('Selling Training')
  })
})

describe('correctPipelineTypeCondition', () => {
  it('rewrites the stale Selling Training condition', () => {
    expect(
      correctPipelineTypeCondition('eval:doc.pipeline_type == "Training"'),
    ).toBe('eval:doc.pipeline_type == "Selling Training"')
  })

  it('preserves the operator and single-quote style', () => {
    expect(
      correctPipelineTypeCondition("eval:doc.pipeline_type!='Training'"),
    ).toBe("eval:doc.pipeline_type!='Selling Training'")
  })

  it('corrects a stale condition combined with others', () => {
    expect(
      correctPipelineTypeCondition(
        'eval:doc.pipeline_type == "Training" && doc.status == "Training submitted"',
      ),
    ).toBe(
      'eval:doc.pipeline_type == "Selling Training" && doc.status == "Training submitted"',
    )
  })

  it('leaves the real pipeline values untouched', () => {
    for (const value of [
      'Individual Session',
      'Workshop',
      'Selling Training',
      'Delivering Coaching',
    ]) {
      const expr = `eval:doc.pipeline_type == "${value}"`
      expect(correctPipelineTypeCondition(expr)).toBe(expr)
    }
  })

  it('never touches a status literal that merely contains "Training"', () => {
    const expr = 'eval:doc.status == "Training submitted"'
    expect(correctPipelineTypeCondition(expr)).toBe(expr)
  })

  it('is a no-op for expressions without a pipeline_type comparison', () => {
    expect(correctPipelineTypeCondition('eval:doc.linkedin')).toBe(
      'eval:doc.linkedin',
    )
    expect(correctPipelineTypeCondition('')).toBe('')
    expect(correctPipelineTypeCondition(undefined)).toBe(undefined)
  })
})

describe('applyPipelineDependencies', () => {
  it('corrects stale conditions on both fields and sections, in place', () => {
    const sections = [
      {
        name: 'training_section',
        depends_on: 'eval:doc.pipeline_type == "Training"',
        columns: [
          {
            fields: [
              {
                fieldname: 'custom_training_owner',
                depends_on: 'eval:doc.pipeline_type == "Training"',
              },
              {
                fieldname: 'custom_delivery_coach',
                depends_on: 'eval:doc.pipeline_type == "Delivering Coaching"',
              },
              { fieldname: 'deal_value' },
            ],
          },
        ],
      },
      { name: 'contacts_section', columns: [{ fields: [] }] },
    ]

    const result = applyPipelineDependencies(sections)

    expect(result).toBe(sections)
    expect(sections[0].depends_on).toBe(
      'eval:doc.pipeline_type == "Selling Training"',
    )
    const fields = sections[0].columns[0].fields
    expect(fields[0].depends_on).toBe(
      'eval:doc.pipeline_type == "Selling Training"',
    )
    // An already-correct pipeline condition is left alone.
    expect(fields[1].depends_on).toBe(
      'eval:doc.pipeline_type == "Delivering Coaching"',
    )
    // A field without depends_on is untouched.
    expect(fields[2].depends_on).toBeUndefined()
  })

  it('tolerates a missing or malformed layout', () => {
    expect(applyPipelineDependencies(undefined)).toBeUndefined()
    expect(applyPipelineDependencies([])).toEqual([])
    expect(applyPipelineDependencies([{ name: 'x' }])).toEqual([{ name: 'x' }])
  })
})
