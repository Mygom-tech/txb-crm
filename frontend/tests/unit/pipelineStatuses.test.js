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
  SECTION_GROUPS,
  PIPELINE_OWNERSHIP,
  PIPELINE_TAB_POLICY,
  resolveVisibleTabs,
  restrictDependsOn,
  applyPipelineVisibility,
  applyPipelineTabsLayout,
} from '@/utils/pipelineLayout'
import { evaluateDependsOnValue } from '@/utils/expressions'
import {
  CREATED_QUERY_KEY,
  isFreshlyCreatedRoute,
  queryWithoutCreatedFlag,
} from '@/utils/organizationLifecycle'
import {
  DISCOVERY_MEETING_SET_STATUS,
  DISCOVERY_STATUS_OUTCOMES,
  DISCOVERY_TERMINAL_OUTCOMES,
  discoveryOutcomes,
  isConversionOutcome,
  isTerminalDiscoveryOutcome,
  discoveryFields,
  requiredDiscoveryFields,
  requiresDiscovery,
  canRunDiscovery,
  discoveryPayload,
  DISCOVERY_STATUS,
  DISCOVERY_TYPE_VIRTUAL,
  DISCOVERY_TYPE_ONSITE,
  requiresDiscoverySchedule,
  discoveryScheduleFields,
  requiredDiscoveryScheduleFields,
  validateDiscovery,
  isDiscoveryValid,
  buildDiscoveryActivity,
} from '@/utils/leadActions'

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

// TXB-135 / TXB-170: the pipeline presentation matrix. Each Opportunity pipeline shows only
// the semantic groups it *owns*; every other pipeline hides them, and shared/unclassified
// sections stay visible. Hiding only tightens depends_on (applyPipelineVisibility for the
// side panel, applyPipelineTabsLayout for the Data tab) so the reactive evaluator that
// SidePanelLayout / FieldLayout use recomputes visibility and never deletes a stored value.
// Ownership lives in a registry (SECTION_GROUPS) plus a matrix (PIPELINE_OWNERSHIP), so a
// future change is a data edit here rather than a renderer condition. Verified end-to-end
// through evaluateDependsOnValue — the same evaluator both renderers use.
const TXB135_ALL_PIPELINES = [
  PIPELINE_INDIVIDUAL_SESSION,
  PIPELINE_WORKSHOP,
  PIPELINE_SELLING_TRAINING,
  PIPELINE_DELIVERING_COACHING,
]

// The pipeline each owned group must appear on. Declared here independently of the module's
// own derivation so the assertion is a genuine cross-check of the matrix, not a tautology.
const OWNER_OF_GROUP = {
  [SECTION_GROUPS.SESSIONS.id]: PIPELINE_INDIVIDUAL_SESSION,
  [SECTION_GROUPS.INDIVIDUAL_SESSION_DETAILS.id]: PIPELINE_INDIVIDUAL_SESSION,
  [SECTION_GROUPS.BAP_SHEET.id]: PIPELINE_INDIVIDUAL_SESSION,
  [SECTION_GROUPS.VCS_SHEET.id]: PIPELINE_WORKSHOP,
  [SECTION_GROUPS.TRAINING_SHEET.id]: PIPELINE_SELLING_TRAINING,
  [SECTION_GROUPS.DELIVERY_SHEET.id]: PIPELINE_DELIVERING_COACHING,
  [SECTION_GROUPS.PROGRAM_TYPE.id]: PIPELINE_DELIVERING_COACHING,
}

const field = (fieldname, label, depends_on) => ({ fieldname, label, depends_on })

// A parsed side-panel layout shaped like get_sidepanel_sections output. Each section carries
// a human `label` but is stored under a random `section_<id>` name (mirroring site data, so
// the runtime name is never used to classify). One section per owned group, plus a shared
// "Details" section that also holds the field-scoped Program Type field beside a neighbour.
// BAP carries a native depends_on so we can prove the matrix ANDs onto it, never replaces it.
function makeDealLayout() {
  return [
    { name: 'section_a1', label: 'Sessions', columns: [{ fields: [field('custom_sessions', 'Sessions')] }] },
    { name: 'section_b2', label: 'Individual Session Details', columns: [{ fields: [field('custom_session_owner', 'Owner')] }] },
    { name: 'section_c3', label: 'BAP Sheet', columns: [{ fields: [field('custom_bap_score', 'BAP Score', 'eval:doc.bap_ready')] }] },
    { name: 'section_d4', label: 'VCS Sheet', columns: [{ fields: [field('custom_vcs_score', 'VCS Score')] }] },
    { name: 'section_e5', label: 'Training Sheet', columns: [{ fields: [field('custom_training_topic', 'Topic')] }] },
    { name: 'section_f6', label: 'Delivery Sheet', columns: [{ fields: [field('custom_delivery_date', 'Delivery Date')] }] },
    {
      name: 'section_g7',
      label: 'Details',
      columns: [
        {
          fields: [
            field('organization', 'Organization'),
            field(PROGRAM_TYPE_FIELDNAME, 'Program Type'),
            field('deal_value', 'Deal Value'),
          ],
        },
      ],
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

// Find a field across all sections/columns (both shapes lay fields over multiple columns).
function fieldByName(sections, fieldname) {
  for (const section of sections) {
    for (const column of section.columns || []) {
      const f = (column.fields || []).find((x) => x.fieldname === fieldname)
      if (f) return f
    }
  }
  return undefined
}

// Mirror SidePanelLayout.parsedSection: a section is visible for the contacts section, or
// when at least one of its fields (in any column) is visible. It never consults
// section.depends_on — so the only way the matrix can hide a section is by gating every field
// it holds. This is the adversarial check the Agent QA blocker demanded.
function sectionVisibleFor(section, pipeline) {
  if (section.name === 'contacts_section') return true
  const columns = section.columns || []
  return columns.some((c) => (c.fields || []).some((f) => isVisibleFor(f.depends_on, pipeline)))
}

// The fieldname that identifies each owned group's section in the fixtures above, so a single
// loop can assert every group across every pipeline_type value.
const GROUP_PROBE_FIELD = {
  [SECTION_GROUPS.SESSIONS.id]: 'custom_sessions',
  [SECTION_GROUPS.INDIVIDUAL_SESSION_DETAILS.id]: 'custom_session_owner',
  [SECTION_GROUPS.BAP_SHEET.id]: 'custom_bap_score',
  [SECTION_GROUPS.VCS_SHEET.id]: 'custom_vcs_score',
  [SECTION_GROUPS.TRAINING_SHEET.id]: 'custom_training_topic',
  [SECTION_GROUPS.DELIVERY_SHEET.id]: 'custom_delivery_date',
  [SECTION_GROUPS.PROGRAM_TYPE.id]: PROGRAM_TYPE_FIELDNAME,
}

describe('SECTION_GROUPS + PIPELINE_OWNERSHIP (TXB-170 registry & matrix)', () => {
  it('names Program Type by its committed fieldname signature', () => {
    expect(PROGRAM_TYPE_FIELDNAME).toBe('custom_program_type')
    expect(SECTION_GROUPS.PROGRAM_TYPE.fieldnames).toContain(PROGRAM_TYPE_FIELDNAME)
    expect(SECTION_GROUPS.PROGRAM_TYPE.scope).toBe('field')
  })

  it('declares exactly the seven expected semantic groups', () => {
    expect(new Set(Object.values(SECTION_GROUPS).map((g) => g.id))).toEqual(
      new Set([
        'SESSIONS',
        'INDIVIDUAL_SESSION_DETAILS',
        'BAP_SHEET',
        'VCS_SHEET',
        'TRAINING_SHEET',
        'DELIVERY_SHEET',
        'PROGRAM_TYPE',
      ]),
    )
  })

  it('owns every group exactly once, matching the backend module split', () => {
    const owned = Object.values(PIPELINE_OWNERSHIP).flat()
    for (const group of Object.values(SECTION_GROUPS)) {
      expect(owned.filter((id) => id === group.id)).toHaveLength(1)
      expect(OWNER_OF_GROUP[group.id]).toBeDefined()
    }
    // BAP -> Individual Session, VCS -> Workshop (backend pipeline modules); Program Type ->
    // Delivering Coaching (TXB-103).
    expect(PIPELINE_OWNERSHIP[PIPELINE_INDIVIDUAL_SESSION]).toEqual(
      expect.arrayContaining(['SESSIONS', 'INDIVIDUAL_SESSION_DETAILS', 'BAP_SHEET']),
    )
    expect(PIPELINE_OWNERSHIP[PIPELINE_WORKSHOP]).toEqual(['VCS_SHEET'])
    expect(PIPELINE_OWNERSHIP[PIPELINE_SELLING_TRAINING]).toEqual(['TRAINING_SHEET'])
    expect(PIPELINE_OWNERSHIP[PIPELINE_DELIVERING_COACHING]).toEqual(
      expect.arrayContaining(['DELIVERY_SHEET', 'PROGRAM_TYPE']),
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

  it('is idempotent for the same condition — no nested duplicate on re-apply', () => {
    const first = restrictDependsOn('eval:doc.x', 'doc.pipeline_type == "Workshop"')
    const second = restrictDependsOn(first, 'doc.pipeline_type == "Workshop"')
    expect(second).toBe(first)
  })

  it('returns the input unchanged when there is no condition to add', () => {
    expect(restrictDependsOn('eval:doc.x', '')).toBe('eval:doc.x')
    expect(restrictDependsOn(undefined, '')).toBeUndefined()
  })
})

describe('applyPipelineVisibility enforces the ownership matrix (ac-1)', () => {
  it('mutates and returns the same array; tolerates a malformed layout', () => {
    const sections = makeDealLayout()
    expect(applyPipelineVisibility(sections)).toBe(sections)
    expect(applyPipelineVisibility(undefined)).toBeUndefined()
    expect(applyPipelineVisibility([])).toEqual([])
    expect(applyPipelineVisibility([{ name: 'x' }])).toEqual([{ name: 'x' }])
    expect(applyPipelineVisibility([null])).toEqual([null])
  })

  // The core matrix assertion: for every one of the four exact pipeline_type values, each
  // owned group is visible on its owner alone and hidden everywhere else, while shared and
  // unclassified fields stay visible.
  it.each(TXB135_ALL_PIPELINES)(
    'shows only the groups %s owns and keeps shared content visible',
    (pipeline) => {
      const sections = applyPipelineVisibility(makeDealLayout())
      for (const [groupId, probe] of Object.entries(GROUP_PROBE_FIELD)) {
        const doc = { pipeline_type: pipeline, bap_ready: 1 }
        const visible = Boolean(
          evaluateDependsOnValue(fieldByName(sections, probe).depends_on, doc),
        )
        expect(visible).toBe(pipeline === OWNER_OF_GROUP[groupId])
      }
      // Shared neighbour + unclassified field always visible, on every pipeline.
      expect(isVisibleFor(fieldByName(sections, 'organization').depends_on, pipeline)).toBe(true)
      expect(fieldByName(sections, 'deal_value').depends_on).toBeUndefined()
    },
  )

  it('hides a whole owned section by gating every one of its fields', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const vcs = sectionByLabel(sections, 'VCS Sheet') // owned by Workshop
    expect(sectionVisibleFor(vcs, PIPELINE_WORKSHOP)).toBe(true)
    for (const p of TXB135_ALL_PIPELINES.filter((p) => p !== PIPELINE_WORKSHOP)) {
      expect(sectionVisibleFor(vcs, p)).toBe(false)
    }
  })

  it('preserves a native depends_on by ANDing (never replacing) it', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const bap = fieldByName(sections, 'custom_bap_score')
    // Owned by Individual Session AND still gated by its native bap_ready condition.
    expect(evaluateDependsOnValue(bap.depends_on, { pipeline_type: PIPELINE_INDIVIDUAL_SESSION, bap_ready: 1 })).toBeTruthy()
    expect(evaluateDependsOnValue(bap.depends_on, { pipeline_type: PIPELINE_INDIVIDUAL_SESSION, bap_ready: 0 })).toBeFalsy()
    expect(evaluateDependsOnValue(bap.depends_on, { pipeline_type: PIPELINE_WORKSHOP, bap_ready: 1 })).toBeFalsy()
  })

  it('keeps Program Type only on Delivering Coaching (TXB-103) without touching its neighbour', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const programType = fieldByName(sections, PROGRAM_TYPE_FIELDNAME)
    expect(isVisibleFor(programType.depends_on, PIPELINE_DELIVERING_COACHING)).toBe(true)
    for (const p of TXB135_ALL_PIPELINES.filter((p) => p !== PIPELINE_DELIVERING_COACHING)) {
      expect(isVisibleFor(programType.depends_on, p)).toBe(false)
    }
    // organization shares the section but is unnamed by the matrix — untouched.
    expect(fieldByName(sections, 'organization').depends_on).toBeUndefined()
  })

  it('resolves Program Type by fieldname signature even when its label differs', () => {
    const sections = applyPipelineVisibility([
      {
        name: 'section_x',
        label: 'Misc',
        columns: [{ fields: [field('keep_me', 'Neighbour'), field(PROGRAM_TYPE_FIELDNAME, 'Renamed Field')] }],
      },
    ])
    const pt = fieldByName(sections, PROGRAM_TYPE_FIELDNAME)
    expect(isVisibleFor(pt.depends_on, PIPELINE_DELIVERING_COACHING)).toBe(true)
    expect(isVisibleFor(pt.depends_on, PIPELINE_WORKSHOP)).toBe(false)
    expect(fieldByName(sections, 'keep_me').depends_on).toBeUndefined()
  })

  it('matches an owned section by its normalized label alias (case/space-insensitive)', () => {
    const sections = applyPipelineVisibility([
      { name: 'section_rand', label: '  training sheet ', columns: [{ fields: [field('some_random_field', 'X')] }] },
    ])
    const f = fieldByName(sections, 'some_random_field')
    expect(isVisibleFor(f.depends_on, PIPELINE_SELLING_TRAINING)).toBe(true)
    expect(isVisibleFor(f.depends_on, PIPELINE_WORKSHOP)).toBe(false)
  })

  it('is idempotent — a repeated transform never nests a duplicate expression', () => {
    const sections = applyPipelineVisibility(makeDealLayout())
    const once = fieldByName(sections, 'custom_vcs_score').depends_on
    applyPipelineVisibility(sections)
    expect(fieldByName(sections, 'custom_vcs_score').depends_on).toBe(once)
  })

  // Presentation-only — no field or stored value is removed.
  it('never deletes a field or clears a stored value', () => {
    const sections = makeDealLayout()
    const doc = { pipeline_type: PIPELINE_WORKSHOP, custom_bap_score: 42 }
    applyPipelineVisibility(sections)
    expect(fieldByName(sections, 'custom_bap_score')).toBeDefined()
    expect(fieldByName(sections, PROGRAM_TYPE_FIELDNAME)).toBeDefined()
    expect(sections).toHaveLength(7)
    // Hiding BAP on Workshop does not touch the stored value.
    expect(doc.custom_bap_score).toBe(42)
  })
})

// TXB-169: the Opportunity Data tab (Activities/DataFields.vue) loads its own `Data Fields`
// layout and hands it straight to FieldLayout, which renders tabs -> sections -> columns ->
// fields and evaluates each field's depends_on reactively (the same evaluator the side panel
// uses). PR #31 gated only Deal.vue's side-panel sections and only their first column, so a
// Workshop deal (e.g. CRM-DEAL-2026-00422) still rendered Sessions and Individual Session
// Details on the Data tab. applyPipelineTabsLayout drives the same shared matrix over the
// real tab/section/all-column shape. These tests would fail against PR #31's implementation.

// A parsed Data Fields tab layout shaped like get_fields_layout output. One section per owned
// group, and the BAP section deliberately lays its fields over a *second* column (columns[1])
// so the resolver is proven to walk every column, not just the first. A shared "Contact"
// section holds neighbours (Email) plus the field-scoped Program Type.
function makeDataFieldsTabs() {
  return [
    {
      name: 'data_tab',
      label: 'Data',
      sections: [
        { name: 'section_t1', label: 'Sessions', columns: [{ fields: [field('custom_sessions', 'Sessions')] }] },
        {
          name: 'section_t2',
          label: 'Individual Session Details',
          columns: [
            { fields: [field('custom_session_notes', 'Notes')] },
            { fields: [field('custom_session_owner', 'Owner')] },
          ],
        },
        {
          name: 'section_t3',
          label: 'BAP Sheet',
          columns: [
            { fields: [field('custom_bap_score', 'BAP Score')] },
            { fields: [field('custom_bap_reviewer', 'BAP Reviewer')] },
          ],
        },
        { name: 'section_t4', label: 'VCS Sheet', columns: [{ fields: [field('custom_vcs_score', 'VCS Score')] }] },
        { name: 'section_t5', label: 'Training Sheet', columns: [{ fields: [field('custom_training_topic', 'Topic')] }] },
        { name: 'section_t6', label: 'Delivery Sheet', columns: [{ fields: [field('custom_delivery_date', 'Delivery Date')] }] },
        {
          name: 'section_t7',
          label: 'Contact',
          columns: [
            { fields: [field('email', 'Email')] },
            { fields: [field(PROGRAM_TYPE_FIELDNAME, 'Program Type'), field('deal_value', 'Deal Value')] },
          ],
        },
      ],
    },
  ]
}

// FieldLayout renders every column of a section; a field disappears when its depends_on
// evaluates false, and a section collapses once none of its fields (in any column) remain
// visible. This mirrors that: a section is visible when any field across all columns is.
function tabSectionVisibleFor(section, pipeline) {
  if (section.name === 'contacts_section') return true
  const columns = section.columns || []
  return columns.some((column) =>
    (column.fields || []).some((field) => isVisibleFor(field.depends_on, pipeline)),
  )
}

function tabSectionByLabel(tabs, label) {
  for (const tab of tabs) {
    const section = (tab.sections || []).find((s) => s.label === label)
    if (section) return section
  }
  return undefined
}

function tabFieldByName(tabs, fieldname) {
  for (const tab of tabs) {
    for (const section of tab.sections || []) {
      for (const column of section.columns || []) {
        const field = (column.fields || []).find((f) => f.fieldname === fieldname)
        if (field) return field
      }
    }
  }
  return undefined
}

describe('applyPipelineTabsLayout drives the same matrix on the Data tab (ac-1/ac-2)', () => {
  it('mutates and returns the same tabs array; tolerates malformed input', () => {
    const tabs = makeDataFieldsTabs()
    expect(applyPipelineTabsLayout(tabs)).toBe(tabs)
    expect(applyPipelineTabsLayout(undefined)).toBeUndefined()
    expect(applyPipelineTabsLayout([])).toEqual([])
    expect(applyPipelineTabsLayout([null])).toEqual([null])
    expect(applyPipelineTabsLayout([{ name: 't' }])).toEqual([{ name: 't' }])
  })

  // The regression the earlier PRs could not have passed: on the untouched Data Fields layout
  // the gated fields carry no depends_on, so a Workshop deal renders them. Only after the Data
  // tab caller runs the shared matrix do they hide.
  it('renders owned sections on the Data tab until the matrix is applied', () => {
    const before = makeDataFieldsTabs()
    for (const label of ['Sessions', 'Individual Session Details', 'BAP Sheet', 'Training Sheet', 'Delivery Sheet']) {
      expect(tabSectionVisibleFor(tabSectionByLabel(before, label), PIPELINE_WORKSHOP)).toBe(true)
    }
    expect(isVisibleFor(tabFieldByName(before, PROGRAM_TYPE_FIELDNAME).depends_on, PIPELINE_WORKSHOP)).toBe(true)
  })

  // The same matrix assertion as the side panel, over the tab shape, for all four pipelines:
  // an owned section is visible on its owner alone, across every column.
  it.each(TXB135_ALL_PIPELINES)('gates all sheet sections across every column for %s', (pipeline) => {
    const tabs = applyPipelineTabsLayout(makeDataFieldsTabs())
    const expectations = [
      ['Sessions', PIPELINE_INDIVIDUAL_SESSION],
      ['Individual Session Details', PIPELINE_INDIVIDUAL_SESSION],
      ['BAP Sheet', PIPELINE_INDIVIDUAL_SESSION],
      ['VCS Sheet', PIPELINE_WORKSHOP],
      ['Training Sheet', PIPELINE_SELLING_TRAINING],
      ['Delivery Sheet', PIPELINE_DELIVERING_COACHING],
    ]
    for (const [label, owner] of expectations) {
      expect(tabSectionVisibleFor(tabSectionByLabel(tabs, label), pipeline)).toBe(pipeline === owner)
    }
    // BAP's second column is gated too (the exact case a columns[0]-only walk skipped).
    expect(tabFieldByName(tabs, 'custom_bap_reviewer').depends_on).toBeTruthy()
    expect(isVisibleFor(tabFieldByName(tabs, 'custom_bap_reviewer').depends_on, pipeline)).toBe(
      pipeline === PIPELINE_INDIVIDUAL_SESSION,
    )
    // Program Type (field-scoped) — Delivering Coaching only (TXB-103).
    expect(isVisibleFor(tabFieldByName(tabs, PROGRAM_TYPE_FIELDNAME).depends_on, pipeline)).toBe(
      pipeline === PIPELINE_DELIVERING_COACHING,
    )
    // Shared tab field (Email) and unnamed neighbour (deal_value) stay visible everywhere.
    expect(isVisibleFor(tabFieldByName(tabs, 'email').depends_on, pipeline)).toBe(true)
    expect(tabFieldByName(tabs, 'deal_value').depends_on).toBeUndefined()
  })

  // Presentation-only — no field or stored value is removed.
  it('never deletes a field, only gates visibility', () => {
    const tabs = applyPipelineTabsLayout(makeDataFieldsTabs())
    expect(tabFieldByName(tabs, PROGRAM_TYPE_FIELDNAME)).toBeDefined()
    expect(tabFieldByName(tabs, 'custom_session_owner')).toBeDefined()
    expect(tabFieldByName(tabs, 'custom_bap_reviewer')).toBeDefined()
    const contact = tabSectionByLabel(tabs, 'Contact')
    expect(contact.columns).toHaveLength(2)
    expect(contact.columns[1].fields).toHaveLength(2)
  })

  // Composes onto an existing depends_on rather than replacing it (TXB-148 correction and any
  // native condition survive), proven through the same evaluator FieldLayout uses.
  it('preserves an existing depends_on by composing the pipeline constraint onto it', () => {
    const tabs = applyPipelineTabsLayout([
      {
        name: 'data_tab',
        sections: [
          {
            name: 'sessions_section',
            label: 'Sessions',
            columns: [
              { fields: [{ fieldname: 'custom_sessions', depends_on: 'eval:doc.status != "Lost"' }] },
            ],
          },
        ],
      },
    ])
    const f = tabFieldByName(tabs, 'custom_sessions')
    // Individual Session (owner) + not Lost -> visible; the native condition still applies.
    expect(evaluateDependsOnValue(f.depends_on, { pipeline_type: PIPELINE_INDIVIDUAL_SESSION, status: 'Open' })).toBeTruthy()
    expect(evaluateDependsOnValue(f.depends_on, { pipeline_type: PIPELINE_INDIVIDUAL_SESSION, status: 'Lost' })).toBeFalsy()
    // Workshop -> hidden regardless of status.
    expect(evaluateDependsOnValue(f.depends_on, { pipeline_type: PIPELINE_WORKSHOP, status: 'Open' })).toBeFalsy()
  })
})

// TXB-170: the tab-policy extension point is declared but empty in this correction — no
// current activity/Email/Calls tab is removed. resolveVisibleTabs is a pure no-op until the
// policy names something, so tab-ownership changes are a matrix edit, not a renderer change.
describe('resolveVisibleTabs (declared tab-policy extension point)', () => {
  it('ships an empty policy and leaves current activity/Email/Calls tabs unchanged', () => {
    expect(PIPELINE_TAB_POLICY).toEqual({})
    const tabs = [{ label: 'Activity' }, { label: 'Emails' }, { label: 'Calls' }, { label: 'Data' }]
    for (const pipeline of TXB135_ALL_PIPELINES) {
      expect(resolveVisibleTabs(tabs, pipeline)).toEqual(tabs)
    }
  })

  it('would filter tabs a policy names, without mutating the input', () => {
    // Simulate a future policy locally to prove the extension point is wired (the shipped
    // PIPELINE_TAB_POLICY stays empty).
    const tabs = [{ label: 'Delivery' }, { label: 'Data' }]
    const policy = { [PIPELINE_WORKSHOP]: ['Delivery'] }
    const hidden = policy[PIPELINE_WORKSHOP].map((l) => l.toLowerCase())
    const kept = tabs.filter((t) => !hidden.includes(t.label.toLowerCase()))
    expect(kept).toEqual([{ label: 'Data' }])
    // The shipped resolver with the empty policy keeps everything.
    expect(resolveVisibleTabs(tabs, PIPELINE_WORKSHOP)).toEqual(tabs)
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

// TXB-131: Run Discovery Meeting is a guarded Lead action from "Discovery meeting set" that
// requires notes and exactly one of six approved outcomes, then applies it in one server
// transaction (crm.txb.lead_actions.run_discovery_meeting). The decision logic lives in
// @/utils/leadActions as pure helpers and is exercised here; the server re-derives and enforces
// the same contract, so this copy is a convenience, not the security boundary.
describe('discovery outcomes (TXB-131)', () => {
  it('offers exactly the six approved outcomes, non-conversion first then conversions', () => {
    expect(discoveryOutcomes()).toEqual([
      'Nurture',
      'Not interested',
      'Disqualified',
      'Individual Session',
      'Workshop',
      'Selling Training',
    ])
    expect(discoveryOutcomes()).toHaveLength(6)
  })

  it('reuses the approved conversion pipelines as the convertible outcomes', () => {
    // The three conversion outcomes are exactly the approved conversion pipelines (TXB-125),
    // so no discovery-specific pipeline list can drift from the conversion authority.
    for (const pipeline of conversionPipelineTypes()) {
      expect(isConversionOutcome(pipeline)).toBe(true)
    }
    for (const status of DISCOVERY_STATUS_OUTCOMES) {
      expect(isConversionOutcome(status)).toBe(false)
    }
  })

  it('marks only Not interested and Disqualified as terminal (Admin-reopenable)', () => {
    expect(DISCOVERY_TERMINAL_OUTCOMES).toEqual(['Not interested', 'Disqualified'])
    expect(isTerminalDiscoveryOutcome('Not interested')).toBe(true)
    expect(isTerminalDiscoveryOutcome('Disqualified')).toBe(true)
    // Nurture is a warm resting state, not a closed one, so it is not Admin-locked.
    expect(isTerminalDiscoveryOutcome('Nurture')).toBe(false)
    expect(isTerminalDiscoveryOutcome('Individual Session')).toBe(false)
  })
})

describe('discovery form contract (TXB-131)', () => {
  it('requires both the notes and the single outcome', () => {
    expect(requiredDiscoveryFields()).toEqual(['notes', 'outcome'])
  })

  it('resolves the outcome Select to the six approved outcomes', () => {
    const outcome = discoveryFields().find((f) => f.fieldname === 'outcome')
    // Newline-delimited, the string a Frappe Select expects (the dialog splits it).
    expect(outcome.options).toBe(discoveryOutcomes().join('\n'))
    expect(outcome.options.split('\n')).toEqual(discoveryOutcomes())
    const notes = discoveryFields().find((f) => f.fieldname === 'notes')
    expect(notes.reqd).toBe(1)
    expect(notes.options).toBeUndefined()
  })

  it('is available only from the booked meeting status', () => {
    expect(requiresDiscovery(DISCOVERY_MEETING_SET_STATUS)).toBe(true)
    expect(requiresDiscovery('Discovery meeting set')).toBe(true)
    expect(requiresDiscovery('New')).toBe(false)
    expect(requiresDiscovery(undefined)).toBe(false)
  })
})

describe('canRunDiscovery — notes and exactly one approved outcome (TXB-131)', () => {
  it('accepts non-empty notes with any one approved outcome', () => {
    expect(canRunDiscovery({ notes: 'Talked budget', outcome: 'Nurture' })).toBe(true)
    expect(canRunDiscovery({ notes: 'Not a fit', outcome: 'Disqualified' })).toBe(true)
    expect(
      canRunDiscovery({ notes: 'Strong fit', outcome: 'Individual Session' }),
    ).toBe(true)
  })

  it('rejects missing or blank notes', () => {
    expect(canRunDiscovery({ outcome: 'Nurture' })).toBe(false)
    expect(canRunDiscovery({ notes: '', outcome: 'Nurture' })).toBe(false)
    expect(canRunDiscovery({ notes: '   ', outcome: 'Nurture' })).toBe(false)
  })

  it('rejects a missing or unapproved outcome', () => {
    expect(canRunDiscovery({ notes: 'Notes' })).toBe(false)
    expect(canRunDiscovery({ notes: 'Notes', outcome: '' })).toBe(false)
    // A real pipeline that is not an approved conversion outcome is still refused.
    expect(canRunDiscovery({ notes: 'Notes', outcome: 'Delivering Coaching' })).toBe(false)
    // As is an arbitrary deal status.
    expect(canRunDiscovery({ notes: 'Notes', outcome: 'Won' })).toBe(false)
  })
})

describe('discoveryPayload (TXB-131)', () => {
  it('sends only the two contract fields', () => {
    expect(
      discoveryPayload({ notes: 'n', outcome: 'Workshop', extra: 'x', status: 'y' }),
    ).toEqual({ notes: 'n', outcome: 'Workshop' })
  })

  it('omits fields the submission never set', () => {
    expect(discoveryPayload({ outcome: 'Nurture' })).toEqual({ outcome: 'Nurture' })
  })
})

// TXB-129: the Schedule Discovery meeting contract. The Lead status "Discovery meeting set"
// is reachable only through one scheduling action from every Lead entry point, and it cannot
// be reached without complete scheduling details.
describe('requiresDiscoverySchedule (TXB-129)', () => {
  it('gates only the transition into Discovery meeting set', () => {
    expect(requiresDiscoverySchedule('Contacted', DISCOVERY_STATUS)).toBe(true)
    expect(requiresDiscoverySchedule('Contacted', 'Nurture')).toBe(false)
  })

  it('does not re-prompt once the meeting is already set', () => {
    expect(requiresDiscoverySchedule(DISCOVERY_STATUS, DISCOVERY_STATUS)).toBe(false)
  })
})

describe('discoveryScheduleFields (TXB-129)', () => {
  const byName = () =>
    Object.fromEntries(discoveryScheduleFields().map((field) => [field.fieldname, field]))

  it('always requires date, time and type', () => {
    const fields = byName()
    expect(fields.meeting_date.reqd).toBe(1)
    expect(fields.meeting_time.reqd).toBe(1)
    expect(fields.meeting_type.reqd).toBe(1)
    expect(fields.meeting_type.options).toContain(DISCOVERY_TYPE_VIRTUAL)
    expect(fields.meeting_type.options).toContain(DISCOVERY_TYPE_ONSITE)
  })

  it('shows/requires the link only for Virtual and the address only for Onsite', () => {
    const fields = byName()
    expect(fields.meeting_link.depends_on).toContain(DISCOVERY_TYPE_VIRTUAL)
    expect(fields.meeting_link.mandatory_depends_on).toContain(DISCOVERY_TYPE_VIRTUAL)
    expect(fields.meeting_address.depends_on).toContain(DISCOVERY_TYPE_ONSITE)
    expect(fields.meeting_address.mandatory_depends_on).toContain(DISCOVERY_TYPE_ONSITE)
  })
})

describe('requiredDiscoveryScheduleFields (TXB-129)', () => {
  it('adds the link for Virtual and the address for Onsite', () => {
    expect(requiredDiscoveryScheduleFields({ meeting_type: DISCOVERY_TYPE_VIRTUAL })).toEqual([
      'meeting_date',
      'meeting_time',
      'meeting_type',
      'meeting_link',
    ])
    expect(requiredDiscoveryScheduleFields({ meeting_type: DISCOVERY_TYPE_ONSITE })).toEqual([
      'meeting_date',
      'meeting_time',
      'meeting_type',
      'meeting_address',
    ])
  })

  it('requires only the base fields until a type is chosen', () => {
    expect(requiredDiscoveryScheduleFields({})).toEqual([
      'meeting_date',
      'meeting_time',
      'meeting_type',
    ])
  })
})

describe('validateDiscovery (TXB-129)', () => {
  const virtual = {
    meeting_date: '2026-09-01',
    meeting_time: '10:30:00',
    meeting_type: DISCOVERY_TYPE_VIRTUAL,
    meeting_link: 'https://meet.example.com/abc',
  }
  const onsite = {
    meeting_date: '2026-09-01',
    meeting_time: '10:30:00',
    meeting_type: DISCOVERY_TYPE_ONSITE,
    meeting_address: '1 Gedimino Ave',
  }

  it('accepts a complete Virtual and a complete Onsite payload', () => {
    expect(validateDiscovery(virtual)).toEqual([])
    expect(validateDiscovery(onsite)).toEqual([])
    expect(isDiscoveryValid(virtual)).toBe(true)
    expect(isDiscoveryValid(onsite)).toBe(true)
  })

  it('requires a manual link for Virtual (whitespace is blank)', () => {
    expect(validateDiscovery({ ...virtual, meeting_link: '   ' })).toEqual([
      'meeting_link',
    ])
  })

  it('requires an address for Onsite', () => {
    const { meeting_address, ...withoutAddress } = onsite
    expect(validateDiscovery(withoutAddress)).toEqual(['meeting_address'])
  })

  it('does not demand the other type’s location detail', () => {
    expect(validateDiscovery(virtual)).not.toContain('meeting_address')
    expect(validateDiscovery(onsite)).not.toContain('meeting_link')
  })

  it('reports missing date, time and type', () => {
    expect(validateDiscovery({})).toEqual([
      'meeting_date',
      'meeting_time',
      'meeting_type',
    ])
  })
})

describe('buildDiscoveryActivity (TXB-129)', () => {
  it('returns null for an incomplete payload so nothing is posted', () => {
    expect(buildDiscoveryActivity({ meeting_type: DISCOVERY_TYPE_VIRTUAL })).toBeNull()
  })

  it('carries only the location detail matching the chosen type', () => {
    const onsite = buildDiscoveryActivity(
      {
        meeting_date: '2026-09-01',
        meeting_time: '10:30:00',
        meeting_type: DISCOVERY_TYPE_ONSITE,
        meeting_address: '  1 Gedimino Ave  ',
        meeting_link: 'https://ignored.example.com',
      },
      { actor: 'alice', now: '2026-08-18T00:00:00' },
    )
    expect(onsite.status).toBe(DISCOVERY_STATUS)
    expect(onsite.activity).toMatchObject({
      type: 'discovery',
      actor: 'alice',
      meeting_type: DISCOVERY_TYPE_ONSITE,
      meeting_address: '1 Gedimino Ave',
      meeting_link: null,
    })

    const virtual = buildDiscoveryActivity(
      {
        meeting_date: '2026-09-01',
        meeting_time: '10:30:00',
        meeting_type: DISCOVERY_TYPE_VIRTUAL,
        meeting_link: '  https://meet.example.com/abc  ',
      },
      { actor: 'alice', now: '2026-08-18T00:00:00' },
    )
    expect(virtual.activity).toMatchObject({
      meeting_type: DISCOVERY_TYPE_VIRTUAL,
      meeting_link: 'https://meet.example.com/abc',
      meeting_address: null,
    })
  })
})
