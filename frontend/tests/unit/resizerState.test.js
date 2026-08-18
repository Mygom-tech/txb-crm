import { beforeEach, describe, expect, it } from 'vitest'
import {
  SIDEBAR_ENTITIES,
  SIDEBAR_MIN_WIDTH,
  SIDEBAR_MAX_WIDTH,
  DESKTOP_BREAKPOINT,
  widthKeyFor,
  clampWidth,
  responsiveDefaultWidth,
  loadEntitySidebarWidth,
  saveEntitySidebarWidth,
  entitySidebarWidth,
  loadSidebarWidth,
  saveSidebarWidth,
} from '@/utils/resizerState'

const LIMITS = {
  minWidth: SIDEBAR_MIN_WIDTH,
  maxWidth: SIDEBAR_MAX_WIDTH,
  viewportWidth: 1440,
}

beforeEach(() => {
  localStorage.clear()
})

describe('widthKeyFor', () => {
  it('namespaces each entity under a distinct key', () => {
    expect(widthKeyFor(SIDEBAR_ENTITIES.deal)).toBe('crm.sidebar.deal.width')
    expect(widthKeyFor(SIDEBAR_ENTITIES.lead)).toBe('crm.sidebar.lead.width')
    expect(widthKeyFor(SIDEBAR_ENTITIES.organization)).toBe(
      'crm.sidebar.organization.width',
    )
    expect(widthKeyFor(SIDEBAR_ENTITIES.contact)).toBe(
      'crm.sidebar.contact.width',
    )
  })

  it('produces a unique key per entity', () => {
    const keys = new Set(
      Object.values(SIDEBAR_ENTITIES).map((e) => widthKeyFor(e)),
    )
    expect(keys.size).toBe(Object.values(SIDEBAR_ENTITIES).length)
  })
})

describe('clampWidth', () => {
  it('returns null for non-finite input so callers can detect invalid values', () => {
    expect(clampWidth('not a number', LIMITS)).toBeNull()
    expect(clampWidth(NaN, LIMITS)).toBeNull()
    expect(clampWidth(Infinity, LIMITS)).toBeNull()
    expect(clampWidth(null, LIMITS)).toBeNull()
    expect(clampWidth(undefined, LIMITS)).toBeNull()
  })

  it('clamps below the minimum up to the floor', () => {
    expect(clampWidth(SIDEBAR_MIN_WIDTH - 100, LIMITS)).toBe(SIDEBAR_MIN_WIDTH)
  })

  it('clamps above the maximum down to the ceiling', () => {
    expect(clampWidth(SIDEBAR_MAX_WIDTH + 100, LIMITS)).toBe(SIDEBAR_MAX_WIDTH)
  })

  it('passes through a numeric string within range', () => {
    expect(clampWidth('400', LIMITS)).toBe(400)
  })

  it('caps the ceiling at the viewport fraction so a wide value cannot overflow', () => {
    // 90% of a 400px viewport = 360, below the component max of 480.
    expect(clampWidth(SIDEBAR_MAX_WIDTH, { ...LIMITS, viewportWidth: 400 })).toBe(
      360,
    )
  })

  it('never lets the viewport cap push the ceiling below the floor', () => {
    expect(
      clampWidth(SIDEBAR_MAX_WIDTH, { ...LIMITS, viewportWidth: 100 }),
    ).toBe(SIDEBAR_MIN_WIDTH)
  })
})

describe('responsiveDefaultWidth', () => {
  it('is wider on desktop viewports', () => {
    expect(responsiveDefaultWidth(DESKTOP_BREAKPOINT, LIMITS)).toBe(420)
    expect(responsiveDefaultWidth(1920, LIMITS)).toBe(420)
  })

  it('is narrower on compact viewports below the desktop breakpoint', () => {
    expect(responsiveDefaultWidth(DESKTOP_BREAKPOINT - 1, LIMITS)).toBe(352)
    expect(responsiveDefaultWidth(768, LIMITS)).toBe(352)
  })

  it('treats a missing viewport as compact', () => {
    expect(responsiveDefaultWidth(undefined, LIMITS)).toBe(352)
  })
})

describe('entity width namespace isolation', () => {
  it('keeps each entity width under its own key', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.deal, 300)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.lead, 340)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.organization, 380)

    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.deal, LIMITS)).toBe(300)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(340)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.organization, LIMITS)).toBe(
      380,
    )
  })

  it('does not bleed one entity width into another', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.lead, 340)

    // Deal and Organization have nothing saved, so each falls back to its
    // own responsive default rather than reading the Lead preference.
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.deal, LIMITS)).toBe(
      responsiveDefaultWidth(LIMITS.viewportWidth, LIMITS),
    )
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.organization, LIMITS)).toBe(
      responsiveDefaultWidth(LIMITS.viewportWidth, LIMITS),
    )
  })

  it('does not touch unrelated localStorage keys', () => {
    localStorage.setItem('unrelated', 'keep-me')
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.deal, 300)
    expect(localStorage.getItem('unrelated')).toBe('keep-me')
  })
})

describe('loadEntitySidebarWidth restoration', () => {
  it('falls back to the responsive default when nothing is stored', () => {
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(420)
    expect(
      loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, {
        ...LIMITS,
        viewportWidth: 800,
      }),
    ).toBe(352)
  })

  it('restores a valid saved width unchanged', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.organization), '400')
    expect(
      loadEntitySidebarWidth(SIDEBAR_ENTITIES.organization, LIMITS),
    ).toBe(400)
  })

  it('restores the responsive default for a corrupt (invalid) saved width', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.lead), 'corrupt')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(420)
  })

  it('clamps an out-of-range (too small) saved width to the minimum', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.lead), '10')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(
      SIDEBAR_MIN_WIDTH,
    )
  })

  it('clamps an out-of-range (too large) saved width to the maximum', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.lead), '99999')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(
      SIDEBAR_MAX_WIDTH,
    )
  })
})

describe('saveEntitySidebarWidth', () => {
  it('persists a finite width under the entity key', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.organization, 360)
    expect(
      localStorage.getItem(widthKeyFor(SIDEBAR_ENTITIES.organization)),
    ).toBe('360')
  })

  it('ignores non-finite widths', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.organization, NaN)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.organization, 'nope')
    expect(
      localStorage.getItem(widthKeyFor(SIDEBAR_ENTITIES.organization)),
    ).toBeNull()
  })
})

describe('entitySidebarWidth binding', () => {
  it('exposes a load/save pair bound to one entity namespace', () => {
    const lead = entitySidebarWidth(SIDEBAR_ENTITIES.lead)
    const org = entitySidebarWidth(SIDEBAR_ENTITIES.organization)

    lead.save(300)
    expect(lead.load(LIMITS)).toBe(300)
    // The other entity is unaffected and uses its default.
    expect(org.load(LIMITS)).toBe(
      responsiveDefaultWidth(LIMITS.viewportWidth, LIMITS),
    )
  })
})

describe('Contact sidebar width persistence', () => {
  it('namespaces the Contact width under its own key', () => {
    expect(widthKeyFor(SIDEBAR_ENTITIES.contact)).toBe(
      'crm.sidebar.contact.width',
    )
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.contact, 360)
    expect(
      localStorage.getItem(widthKeyFor(SIDEBAR_ENTITIES.contact)),
    ).toBe('360')
  })

  it('restores a valid saved Contact width unchanged', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.contact), '400')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(400)
  })

  it('falls back to the responsive default when nothing is stored', () => {
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(420)
    expect(
      loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, {
        ...LIMITS,
        viewportWidth: 800,
      }),
    ).toBe(352)
  })

  it('restores the responsive default for a corrupt (invalid) saved width', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.contact), 'corrupt')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(420)
  })

  it('clamps an out-of-range (too small) saved width to the minimum', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.contact), '10')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(
      SIDEBAR_MIN_WIDTH,
    )
  })

  it('clamps an out-of-range (too large) saved width to the maximum', () => {
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.contact), '99999')
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(
      SIDEBAR_MAX_WIDTH,
    )
  })

  it('clamps a restored Contact width to the narrower viewport', () => {
    // 90% of a 400px viewport = 360, below the component max of 480.
    localStorage.setItem(widthKeyFor(SIDEBAR_ENTITIES.contact), '480')
    expect(
      loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, {
        ...LIMITS,
        viewportWidth: 400,
      }),
    ).toBe(360)
  })

  it('keeps the Contact width isolated from other entity namespaces', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.deal, 300)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.lead, 340)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.organization, 380)
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.contact, 410)

    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(410)
    // Saving Contact never overwrites the other namespaces.
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.deal, LIMITS)).toBe(300)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.lead, LIMITS)).toBe(340)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.organization, LIMITS)).toBe(
      380,
    )
  })

  it('does not read another entity width when Contact has nothing saved', () => {
    saveEntitySidebarWidth(SIDEBAR_ENTITIES.lead, 340)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.contact, LIMITS)).toBe(
      responsiveDefaultWidth(LIMITS.viewportWidth, LIMITS),
    )
  })

  it('exposes a load/save pair bound to the Contact namespace', () => {
    const contact = entitySidebarWidth(SIDEBAR_ENTITIES.contact)
    contact.save(300)
    expect(contact.load(LIMITS)).toBe(300)
    expect(localStorage.getItem(widthKeyFor(SIDEBAR_ENTITIES.contact))).toBe(
      '300',
    )
  })
})

describe('Deal backward-compatible wrappers', () => {
  it('read and write the same namespaced Deal key as the entity API', () => {
    saveSidebarWidth(320)
    expect(loadSidebarWidth(LIMITS)).toBe(320)
    expect(loadEntitySidebarWidth(SIDEBAR_ENTITIES.deal, LIMITS)).toBe(320)
    expect(localStorage.getItem(widthKeyFor(SIDEBAR_ENTITIES.deal))).toBe('320')
  })
})
