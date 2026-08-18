import { describe, expect, it } from 'vitest'
import { expandDealContacts } from '@/utils/dealContacts'

describe('expandDealContacts', () => {
  it('marks every loaded contact card as expanded', () => {
    const data = [
      { name: 'CONTACT-1', opened: false },
      { name: 'CONTACT-2' },
      { name: 'CONTACT-3', opened: undefined },
    ]
    const result = expandDealContacts(data)
    expect(result.every((c) => c.opened === true)).toBe(true)
  })

  it('returns the same array reference (transform in place)', () => {
    const data = [{ name: 'CONTACT-1' }]
    expect(expandDealContacts(data)).toBe(data)
  })

  it('preserves the other contact fields', () => {
    const data = [{ name: 'CONTACT-1', email: 'a@b.co', phone: '123' }]
    const [contact] = expandDealContacts(data)
    expect(contact).toMatchObject({
      name: 'CONTACT-1',
      email: 'a@b.co',
      phone: '123',
      opened: true,
    })
  })

  it('does not force a card to stay open: opened is still writable afterwards', () => {
    const [contact] = expandDealContacts([{ name: 'CONTACT-1' }])
    expect(contact.opened).toBe(true)
    // The per-card chevron toggles opened; the transform never locks it.
    contact.opened = false
    expect(contact.opened).toBe(false)
  })

  it('handles an empty list', () => {
    expect(expandDealContacts([])).toEqual([])
  })

  it('passes non-array input through untouched', () => {
    expect(expandDealContacts(null)).toBeNull()
    expect(expandDealContacts(undefined)).toBeUndefined()
    const obj = { not: 'an array' }
    expect(expandDealContacts(obj)).toBe(obj)
  })
})
