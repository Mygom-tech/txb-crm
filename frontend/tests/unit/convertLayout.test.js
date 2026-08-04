import { describe, it, expect } from 'vitest'
import {
  excludeSelfRenderedFields,
  SELF_RENDERED_FIELDS,
} from '@/utils/convertLayout'

const tabsWith = (...fieldnames) => [
  {
    name: 'first_tab',
    sections: [
      {
        name: 'required_fields_section_abcd',
        columns: [
          {
            name: 'col',
            fields: fieldnames.map((fieldname) => ({ fieldname })),
          },
        ],
      },
    ],
  },
]

describe('excludeSelfRenderedFields', () => {
  it('drops the synthesised status field, leaving no section to render', () => {
    // status is the only reqd field without a default on CRM Deal, so the synthesised
    // section holds nothing else -- and [] is how FieldLayout is told not to render.
    expect(
      excludeSelfRenderedFields(tabsWith('status'), SELF_RENDERED_FIELDS),
    ).toEqual([])
  })

  it('drops pipeline_type too, should it ever become required', () => {
    const result = excludeSelfRenderedFields(
      tabsWith('pipeline_type', 'deal_value'),
      SELF_RENDERED_FIELDS,
    )
    expect(result[0].sections[0].columns[0].fields).toEqual([
      { fieldname: 'deal_value' },
    ])
  })

  it('keeps unrelated required fields', () => {
    const result = excludeSelfRenderedFields(
      tabsWith('deal_value', 'close_date'),
      SELF_RENDERED_FIELDS,
    )
    expect(result[0].sections[0].columns[0].fields).toHaveLength(2)
  })

  it('does not mutate the layout it was given', () => {
    // dealTabs.data is the resource's own cached value; a transform that edited it in
    // place would corrupt the cache the way stores/meta.js does.
    const tabs = tabsWith('status', 'deal_value')
    excludeSelfRenderedFields(tabs, SELF_RENDERED_FIELDS)
    expect(tabs[0].sections[0].columns[0].fields).toHaveLength(2)
  })

  it('tolerates the empty and undefined layouts the endpoint can return', () => {
    expect(excludeSelfRenderedFields(undefined, SELF_RENDERED_FIELDS)).toEqual(
      [],
    )
    expect(excludeSelfRenderedFields([], SELF_RENDERED_FIELDS)).toEqual([])
    expect(
      excludeSelfRenderedFields(
        [{ name: 'first_tab', sections: [] }],
        SELF_RENDERED_FIELDS,
      ),
    ).toEqual([])
  })
})
