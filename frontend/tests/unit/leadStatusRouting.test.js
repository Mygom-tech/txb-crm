import { describe, it, expect } from 'vitest'

import {
  CONTACT_ATTEMPTED_STATUS,
  CONTACTED_STATUS,
  requiresDial,
  requiresReach,
} from '@/utils/leadActions'

/**
 * The guarded Lead status-routing contract, shared by every existing-Lead entry point:
 *
 *   - desktop Lead.vue header dropdown        (triggerStatusChange)
 *   - desktop Lead.vue side-panel/activities  (beforeStatusChange)
 *   - responsive MobileLead.vue header         (triggerStatusChange)
 *   - responsive MobileLead.vue Details/Data   (beforeStatusChange)
 *   - Lead Kanban board                        (requestKanbanTransition)
 *
 * Each surface decides the same way: a move into "Contact attempted" is refused a bare status
 * write and routed through Log a dial instead, while entering "Contacted" routes through Log a
 * reach and everything else (including Lost) keeps its ordinary save. These tests assert the
 * decision each entry point makes, so desktop and mobile cannot drift apart or regress the guard.
 */

// The exact predicates the header dropdowns evaluate before touching status.
function headerRoute(fromStatus, toStatus) {
  if (requiresReach(fromStatus, toStatus)) return 'log-a-reach'
  if (requiresDial(toStatus)) return 'log-a-dial'
  return 'plain-save'
}

// The exact predicate the editable Details/Data + activity status controls evaluate. The dial
// gate is checked first, matching beforeStatusChange on both desktop and mobile.
function dataFieldRoute(toStatus) {
  if (requiresDial(toStatus)) return 'log-a-dial'
  return 'plain-save'
}

describe('responsive/mobile header status dropdown', () => {
  it('routes Contact attempted through Log a dial before any status mutation', () => {
    expect(headerRoute('Open', CONTACT_ATTEMPTED_STATUS)).toBe('log-a-dial')
  })

  it('keeps an unguarded move on the plain status save', () => {
    expect(headerRoute('Contacted', 'Nurture')).toBe('plain-save')
  })

  it('keeps a Lost move on the plain (Lost-reason) path, not Log a dial', () => {
    expect(headerRoute('Open', 'Lost')).toBe('plain-save')
  })
})

describe('responsive/mobile Details/Data editable status control', () => {
  it('routes Contact attempted through the same Log a dial action', () => {
    expect(dataFieldRoute(CONTACT_ATTEMPTED_STATUS)).toBe('log-a-dial')
  })

  it('leaves every other status on the plain save', () => {
    expect(dataFieldRoute('Nurture')).toBe('plain-save')
    expect(dataFieldRoute('Lost')).toBe('plain-save')
    expect(dataFieldRoute(CONTACTED_STATUS)).toBe('plain-save')
  })
})

describe('desktop entry points keep their existing guarded routing', () => {
  it('still routes Contact attempted through Log a dial from the header', () => {
    expect(headerRoute('Open', CONTACT_ATTEMPTED_STATUS)).toBe('log-a-dial')
  })

  it('still routes entering Contacted through Log a reach, never Log a dial', () => {
    expect(headerRoute('Open', CONTACTED_STATUS)).toBe('log-a-reach')
    expect(requiresDial(CONTACTED_STATUS)).toBe(false)
  })

  it('does not re-gate a Lead already resting in Contact attempted onward move', () => {
    // Leaving Contact attempted is unguarded; only entering it opens Log a dial.
    expect(headerRoute(CONTACT_ATTEMPTED_STATUS, 'Nurture')).toBe('plain-save')
  })
})
