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
  PROGRAM_TYPE_FIELDNAME,
  PIPELINE_VISIBILITY_RULES,
  restrictDependsOn,
  applyPipelineVisibility,
} from '@/utils/pipelineLayout'
import { evaluateDependsOnValue } from '@/utils/expressions'
import {
  CREATED_QUERY_KEY,
  isFreshlyCreatedRoute,
  queryWithoutCreatedFlag,
} from '@/utils/organizationLifecycle'

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

// TXB-135: the pipeline presentation matrix. Each Opportunity pipeline shows only the
// approved fields/sections; hiding tightens depends_on (applyPipelineVisibility, applied in
// Deal.vue getParsedSections after the TXB-148 correction) so SidePanelLayout re-evaluates
// it reactively and never deletes a stored value. Verified end-to-end through
// evaluateDependsOnValue — the same evaluator SidePanelLayout uses.
const TXB135_ALL_PIPELINES = [
  PIPELINE_INDIVIDUAL_SESSION,
  PIPELINE_WORKSHOP,
  PIPELINE_SELLING_TRAINING,
  PIPELINE_DELIVERING_COACHING,
]

// A parsed side-panel layout shaped like get_sidepanel_sections output: sections with a
// `label`/`name` and a first column of `fields`. Program Type sits as a field so we can
// assert its per-pipeline visibility.
function makeDealLayout() {
  return [
    {
      name: 'individual_session_details_section',
      label: 'Individual Session Details',
      columns: [{ fields: [{ fieldname: 'custom_session_owner' }] }],
    },
    {
      name: 'sessions_section',
      label: 'Sessions',
      columns: [{ fields: [{ fieldname: 'custom_sessions' }] }],
    },
    {
      name: 'program_section',
      label: 'Program',
      columns: [
        {
          fields: [
            { fieldname: PROGRAM_TYPE_FIELDNAME, label: 'Program Type' },
            { fieldname: 'deal_value' },
          ],
        },
      ],
    },
    // Unrelated section the matrix must leave completely alone.
    {
      name: 'contacts_section',
      label: 'Contacts',
      columns: [{ fields: [{ fieldname: 'organization' }] }],
    },
  ]
}

function isVisibleFor(dependsOn, pipeline) {
  // No depends_on means always shown.
  if (!dependsOn) return true
  return Boolean(evaluateDependsOnValue(dependsOn, { pipeline_type: pipeline }))
}

function sectionByLabel(sections, label) {
  return sections.find((s) => s.label === label)
}

function programTypeField(sections) {
  for (const section of sections) {
    const field = section.columns?.[0]?.fields?.find(
      (f) => f.fieldname === PROGRAM_TYPE_FIELDNAME,
    )
    if (field) return field
  }
  return undefined
}

describe('PIPELINE_VISIBILITY_RULES (TXB-135 presentation matrix)', () => {
  it('names Program Type by its committed fieldname', () => {
    expect(PROGRAM_TYPE_FIELDNAME).toBe('custom_program_type')
  })

  it('hides the individual-session sections on Workshop only', () => {
    const rule = PIPELINE_VISIBILITY_RULES.find((r) =>
      r.labels?.includes('Sessions'),
    )
    expect(rule.labels).toContain('Individual Session Details')
    expect(rule.keepVisibleWhen).toBe(
      `doc.pipeline_type != "${PIPELINE_WORKSHOP}"`,
    )
  })

  it('keeps Program Type only on Delivering Coaching', () => {
    const rule = PIPELINE_VISIBILITY_RULES.find((r) =>
      r.fieldnames?.includes(PROGRAM_TYPE_FIELDNAME),
    )
    expect(rule.keepVisibleWhen).toBe(
      `doc.pipeline_type == "${PIPELINE_DELIVERING_COACHING}"`,
    )
  })
})

describe('restrictDependsOn', () => {
  it('wraps a bare condition in eval when there is no existing depends_on', () => {
    expect(restrictDependsOn(undefined, 'doc.pipeline_type != "Workshop"')).toBe(
      'eval:(doc.pipeline_type != "Workshop")',
    )
    expect(restrictDependsOn('', 'doc.pipeline_type != "Workshop"')).toBe(
      'eval:(doc.pipeline_type != "Workshop")',
    )
  })

  it('ANDs onto an existing eval condition, preserving it', () => {
    expect(
      restrictDependsOn(
        'eval:doc.status == "Session Run"',
        'doc.pipeline_type != "Workshop"',
      ),
    ).toBe(
      'eval:(doc.status == "Session Run") && (doc.pipeline_type != "Workshop")',
    )
  })

  it('treats a plain field dependency as a truthiness check', () => {
    expect(
      restrictDependsOn(
        'custom_has_program',
        'doc.pipeline_type == "Delivering Coaching"',
      ),
    ).toBe(
      'eval:(doc.custom_has_program) && (doc.pipeline_type == "Delivering Coaching")',
    )
  })

  it('does not lose the original visibility for a kept pipeline', () => {
    const combined = restrictDependsOn(
      'eval:doc.status != "Lost"',
      'doc.pipeline_type != "Workshop"',
    )
    // Individual Session, not Lost -> still visible; Workshop -> hidden regardless.
    expect(
      evaluateDependsOnValue(combined, {
        pipeline_type: 'Individual Session',
        status: 'Submitted',
      }),
    ).toBeTruthy()
    expect(
      evaluateDependsOnValue(combined, {
        pipeline_type: 'Individual Session',
        status: 'Lost',
      }),
    ).toBeFalsy()
    expect(
      evaluateDependsOnValue(combined, {
        pipeline_type: 'Workshop',
        status: 'Submitted',
      }),
    ).toBeFalsy()
  })
})

describe('applyPipelineVisibility', () => {
  it('mutates and returns the same array', () => {
    const sections = makeDealLayout()
    expect(applyPipelineVisibility(sections)).toBe(sections)
  })

  it('tolerates a missing or malformed layout', () => {
    expect(applyPipelineVisibility(undefined)).toBeUndefined()
    expect(applyPipelineVisibility([])).toEqual([])
    expect(applyPipelineVisibility([{ name: 'x' }])).toEqual([{ name: 'x' }])
    expect(applyPipelineVisibility([null])).toEqual([null])
  })

  it('leaves sections/fields the matrix does not name untouched', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const contacts = sectionByLabel(sections, 'Contacts')
    expect(contacts.depends_on).toBeUndefined()
    const dealValue = sectionByLabel(sections, 'Program').columns[0].fields[1]
    expect(dealValue.fieldname).toBe('deal_value')
    expect(dealValue.depends_on).toBeUndefined()
  })

  // ─── AC-1: Workshop hides Individual Session Details, Sessions, and Program Type ───
  it('hides Individual Session Details, Sessions and Program Type on Workshop', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const isd = sectionByLabel(sections, 'Individual Session Details')
    const sess = sectionByLabel(sections, 'Sessions')
    const programType = programTypeField(sections)

    expect(isVisibleFor(isd.depends_on, PIPELINE_WORKSHOP)).toBe(false)
    expect(isVisibleFor(sess.depends_on, PIPELINE_WORKSHOP)).toBe(false)
    expect(isVisibleFor(programType.depends_on, PIPELINE_WORKSHOP)).toBe(false)
  })

  // ─── AC-2: Individual Session & Selling Training hide Program Type; ───
  //          Delivering Coaching keeps it (TXB-103).
  it('hides Program Type on Individual Session and Selling Training', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const programType = programTypeField(sections)
    expect(isVisibleFor(programType.depends_on, PIPELINE_INDIVIDUAL_SESSION)).toBe(
      false,
    )
    expect(isVisibleFor(programType.depends_on, PIPELINE_SELLING_TRAINING)).toBe(
      false,
    )
  })

  it('keeps Program Type on Delivering Coaching (TXB-103 placement)', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const programType = programTypeField(sections)
    expect(
      isVisibleFor(programType.depends_on, PIPELINE_DELIVERING_COACHING),
    ).toBe(true)
  })

  it('keeps Individual Session Details and Sessions on every non-Workshop pipeline', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const isd = sectionByLabel(sections, 'Individual Session Details')
    const sess = sectionByLabel(sections, 'Sessions')
    for (const pipeline of TXB135_ALL_PIPELINES.filter(
      (p) => p !== PIPELINE_WORKSHOP,
    )) {
      expect(isVisibleFor(isd.depends_on, pipeline)).toBe(true)
      expect(isVisibleFor(sess.depends_on, pipeline)).toBe(true)
    }
  })

  // ─── AC-3: hiding is presentation-only — the field/value is never removed ───
  it('never deletes fields or values, only gates visibility', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    // The Program Type field still exists in the layout on Workshop; it is only hidden.
    const programType = programTypeField(sections)
    expect(programType).toBeDefined()
    expect(programType.fieldname).toBe(PROGRAM_TYPE_FIELDNAME)
    // Every section and field remains present; only depends_on was added.
    expect(sections).toHaveLength(4)
    expect(sectionByLabel(sections, 'Sessions').columns[0].fields).toHaveLength(1)
  })

  it('matches Program Type by label when it is a standalone section', () => {
    const sections = applyPipelineVisibility([
      {
        name: 'program_type_section',
        label: 'Program Type',
        columns: [{ fields: [{ fieldname: 'custom_program_type_note' }] }],
      },
    ])
    const section = sectionByLabel(sections, 'Program Type')
    expect(isVisibleFor(section.depends_on, PIPELINE_WORKSHOP)).toBe(false)
    expect(isVisibleFor(section.depends_on, PIPELINE_DELIVERING_COACHING)).toBe(
      true,
    )
  })
})

// Native replacement for the `Organization Reload After Create` Form Script (TXB-150).
// OrganizationModal routes a freshly inserted Organization to its page with a one-shot
// `created` route flag; Organization.vue reads it on mount to reconcile the document
// resource with the canonical saved Organization (a scoped reload, no timer / full-page
// reload) and then drops the flag from the router's reactive query. The decision logic
// lives in @/utils/organizationLifecycle and is exercised here as pure functions.
describe('isFreshlyCreatedRoute (Organization Reload After Create port)', () => {
  it('is true only when the one-shot created flag is present', () => {
    expect(isFreshlyCreatedRoute({ [CREATED_QUERY_KEY]: '1' })).toBe(true)
  })

  it('is false for a plain organization route with no flag', () => {
    expect(isFreshlyCreatedRoute({})).toBe(false)
    expect(isFreshlyCreatedRoute(undefined)).toBe(false)
    expect(isFreshlyCreatedRoute(null)).toBe(false)
  })

  it('ignores unrelated query params such as the list view context', () => {
    expect(isFreshlyCreatedRoute({ view: 'all', viewType: 'list' })).toBe(false)
  })

  it('is false when the flag is explicitly empty, so a stripped route never reconciles again', () => {
    expect(isFreshlyCreatedRoute({ [CREATED_QUERY_KEY]: '' })).toBe(false)
  })
})

describe('queryWithoutCreatedFlag (one-shot flag strip)', () => {
  it('drops the created flag, leaving an empty query when nothing else remains', () => {
    expect(queryWithoutCreatedFlag({ [CREATED_QUERY_KEY]: '1' })).toEqual({})
  })

  it('handles an already-clean or missing query object', () => {
    expect(queryWithoutCreatedFlag({})).toEqual({})
    expect(queryWithoutCreatedFlag(undefined)).toEqual({})
    expect(queryWithoutCreatedFlag(null)).toEqual({})
  })

  it('preserves breadcrumb view/viewType params while removing the flag', () => {
    expect(
      queryWithoutCreatedFlag({
        view: 'all',
        [CREATED_QUERY_KEY]: '1',
        viewType: 'list',
      }),
    ).toEqual({ view: 'all', viewType: 'list' })
  })

  it('returns a new object without mutating the source query', () => {
    const source = { [CREATED_QUERY_KEY]: '1', view: 'all' }
    const result = queryWithoutCreatedFlag(source)
    expect(result).not.toBe(source)
    expect(source[CREATED_QUERY_KEY]).toBe('1')
  })
})
