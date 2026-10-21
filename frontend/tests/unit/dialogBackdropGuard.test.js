import { describe, it, expect, vi, afterEach } from 'vitest'
import { handleDialogBackdropPointerDown } from '@/utils/dialogBackdropGuard'

function mount(withDropdown) {
  document.body.innerHTML = `
    <div class="dialog-overlay"></div>
    <div class="dialog-scroll-container">
      <div class="dialog-content" data-dismissable-layer><input /></div>
    </div>
    ${withDropdown ? '<div role="menu" data-dismissable-layer></div>' : ''}`
}

function pointerDownOn(selector) {
  const event = new Event('pointerdown', { bubbles: true })
  Object.defineProperty(event, 'target', {
    value: document.querySelector(selector),
  })
  event.stopImmediatePropagation = vi.fn()
  const escapes = vi.fn()
  document.addEventListener('keydown', escapes)
  handleDialogBackdropPointerDown(event)
  document.removeEventListener('keydown', escapes)
  return {
    swallowed: event.stopImmediatePropagation.mock.calls.length > 0,
    escapes,
  }
}

describe('dialog backdrop guard', () => {
  afterEach(() => (document.body.innerHTML = ''))

  it('swallows a backdrop click and leaves a lone modal alone', () => {
    mount(false)
    const { swallowed, escapes } = pointerDownOn('.dialog-scroll-container')
    expect(swallowed).toBe(true)
    expect(escapes).not.toHaveBeenCalled()
  })

  it('closes a dropdown open inside the modal with Escape', () => {
    mount(true)
    const { swallowed, escapes } = pointerDownOn('.dialog-overlay')
    expect(swallowed).toBe(true)
    expect(escapes).toHaveBeenCalledOnce()
    expect(escapes.mock.calls[0][0].key).toBe('Escape')
  })

  it('never touches clicks inside the modal content', () => {
    mount(true)
    const { swallowed, escapes } = pointerDownOn('.dialog-content input')
    expect(swallowed).toBe(false)
    expect(escapes).not.toHaveBeenCalled()
  })
})
