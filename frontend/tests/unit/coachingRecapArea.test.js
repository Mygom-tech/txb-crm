import { readFileSync } from 'node:fs'

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'

// TXB-276: mount the REAL CoachingRecapArea against faithful frappe-ui stubs. Only the server
// round trip (`call`) and the presentational primitives are replaced; `vueRef` bridges Vue's `h`
// into the hoisted mock factory, as takeAction.test.js does.
const mocks = vi.hoisted(() => ({ call: vi.fn(), toastError: vi.fn(), h: null }))

vi.mock('frappe-ui', () => ({
  call: mocks.call,
  toast: { error: mocks.toastError },
  Badge: {
    name: 'Badge',
    props: ['label', 'theme', 'variant', 'size'],
    setup: (props) => () =>
      mocks.h('span', { 'data-theme': props.theme }, props.label),
  },
  Button: {
    name: 'Button',
    props: ['label', 'loading', 'disabled', 'variant'],
    emits: ['click'],
    setup: (props, { emit }) => () =>
      mocks.h(
        'button',
        {
          type: 'button',
          disabled: !!props.disabled,
          onClick: () => !props.disabled && emit('click'),
        },
        props.label,
      ),
  },
  Dialog: {
    name: 'Dialog',
    props: ['open', 'title'],
    emits: ['update:open', 'close'],
    setup: (props, { slots }) => () =>
      props.open
        ? mocks.h('div', { 'data-test': 'dialog' }, [
            slots.default?.(),
            slots.actions?.(),
          ])
        : null,
  },
}))

vi.mock('@/components/Activities/TimelineTimestamp.vue', () => ({
  default: {
    name: 'TimelineTimestamp',
    props: ['date'],
    setup: (props) => () => mocks.h('time', props.date),
  },
}))

import CoachingRecapArea from '@/components/Activities/CoachingRecapArea.vue'

mocks.h = h

const API = 'crm.txb.api.coaching_call_recap'

function recap(overrides = {}) {
  return {
    name: 'RECAP-0001',
    activity_type: 'coaching_recap',
    recap: 'RECAP-0001',
    note: 'NOTE-1',
    status: 'sent',
    recipient_email: 'client@example.com',
    actor: 'coach@example.com',
    owner_name: 'Casey Coach',
    sent_at: '2026-10-01 10:00:00',
    creation: '2026-10-01 09:00:00',
    revision_of: null,
    last_error: null,
    can_retry: false,
    can_send_revised: false,
    ...overrides,
  }
}

let hosts = []

function mount(activity) {
  const state = reactive({ reloads: 0 })
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp({
    render: () =>
      h(CoachingRecapArea, { activity, onReload: () => state.reloads++ }),
  })
  app.config.globalProperties.__ = globalThis.__
  app.mount(container)
  hosts.push({ app, container })
  return { container, state }
}

const q = (container, test) => container.querySelector(`[data-test="${test}"]`)
const click = async (el) => {
  el.dispatchEvent(new Event('click', { bubbles: true }))
  await nextTick()
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function deferred() {
  let resolve
  const promise = new Promise((r) => (resolve = r))
  return { promise, resolve }
}

beforeEach(() => {
  mocks.call.mockReset()
  mocks.toastError.mockReset()
})

afterEach(() => {
  hosts.forEach(({ app, container }) => {
    app.unmount()
    container.remove()
  })
  hosts = []
})

describe('CoachingRecapArea — status labels (ac-1)', () => {
  it('renders a distinct label and badge per status', () => {
    const rendered = ['queued', 'sent', 'failed', 'opted_out'].map((status) => {
      const { container } = mount(recap({ status }))
      const badge = q(container, 'status')
      return { label: badge.textContent, theme: badge.getAttribute('data-theme') }
    })
    expect(rendered.map((r) => r.label)).toEqual([
      'Queued',
      'Sent',
      'Failed',
      'Opted out',
    ])
    expect(new Set(rendered.map((r) => r.theme)).size).toBe(4)
  })

  it('shows recipient and actor for a sent recap', () => {
    const { container } = mount(recap())
    expect(q(container, 'recipient').textContent).toContain('client@example.com')
    expect(container.textContent).toContain('Casey Coach')
    expect(container.querySelector('time').textContent).toBe('2026-10-01 10:00:00')
  })

  it('names the actor as a deliberate opt-out with no recipient and no actions', () => {
    const { container } = mount(
      recap({
        status: 'opted_out',
        recipient_email: null,
        // Even a mis-flagged opt-out must never offer a send.
        can_retry: true,
        can_send_revised: true,
      }),
    )
    expect(container.textContent).toContain('Casey Coach')
    expect(q(container, 'opt-out').textContent).toContain('opted out')
    expect(q(container, 'recipient')).toBeNull()
    expect(q(container, 'retry')).toBeNull()
    expect(q(container, 'send-revised')).toBeNull()
  })
})

describe('CoachingRecapArea — Retry (ac-2)', () => {
  it('renders Retry only when can_retry is true', () => {
    expect(q(mount(recap({ status: 'failed' })).container, 'retry')).toBeNull()
    expect(
      q(mount(recap({ status: 'failed', can_retry: true })).container, 'retry'),
    ).not.toBeNull()
  })

  it('posts retry_recap once, is disabled while pending and emits reload on success', async () => {
    const pending = deferred()
    mocks.call.mockReturnValue(pending.promise)
    const { container, state } = mount(
      recap({ status: 'failed', can_retry: true, last_error: 'SMTP down' }),
    )
    const button = q(container, 'retry')

    await click(button)
    expect(button.disabled).toBe(true)
    await click(button)

    expect(mocks.call).toHaveBeenCalledTimes(1)
    expect(mocks.call).toHaveBeenCalledWith(`${API}.retry_recap`, {
      recap: 'RECAP-0001',
    })
    expect(state.reloads).toBe(0)

    pending.resolve({ name: 'RECAP-0001', status: 'queued' })
    await flush()
    expect(state.reloads).toBe(1)
    expect(button.disabled).toBe(false)
  })

  it('does not emit reload when the retry fails', async () => {
    mocks.call.mockRejectedValue({ messages: ['Only a failed recap can be retried.'] })
    const { container, state } = mount(recap({ status: 'failed', can_retry: true }))
    await click(q(container, 'retry'))
    await flush()
    expect(state.reloads).toBe(0)
    expect(mocks.toastError).toHaveBeenCalledWith('Only a failed recap can be retried.')
  })
})

describe('CoachingRecapArea — Send revised copy (ac-3)', () => {
  it('renders Send revised copy only when can_send_revised is true', () => {
    expect(q(mount(recap()).container, 'send-revised')).toBeNull()
    expect(
      q(mount(recap({ can_send_revised: true })).container, 'send-revised'),
    ).not.toBeNull()
  })

  it('opens a confirmation and Cancel sends nothing', async () => {
    const { container, state } = mount(recap({ can_send_revised: true }))
    expect(q(container, 'dialog')).toBeNull()

    await click(q(container, 'send-revised'))
    expect(q(container, 'dialog')).not.toBeNull()
    expect(mocks.call).not.toHaveBeenCalled()

    await click(q(container, 'cancel-revised'))
    await flush()
    expect(q(container, 'dialog')).toBeNull()
    expect(mocks.call).not.toHaveBeenCalled()
    expect(state.reloads).toBe(0)
  })

  it('Confirm posts send_revised_copy {recap, confirm: 1} once, then emits reload', async () => {
    const pending = deferred()
    mocks.call.mockReturnValue(pending.promise)
    const { container, state } = mount(recap({ can_send_revised: true }))

    await click(q(container, 'send-revised'))
    const confirm = q(container, 'confirm-revised')
    await click(confirm)
    await click(confirm)

    expect(mocks.call).toHaveBeenCalledTimes(1)
    expect(mocks.call).toHaveBeenCalledWith(`${API}.send_revised_copy`, {
      recap: 'RECAP-0001',
      confirm: 1,
    })
    expect(state.reloads).toBe(0)

    pending.resolve({ name: 'RECAP-0002', status: 'queued', revision_of: 'RECAP-0001' })
    await flush()
    expect(state.reloads).toBe(1)
    expect(q(container, 'dialog')).toBeNull()
  })
})

describe('CoachingRecapArea — revised copies (ac-4)', () => {
  it('marks a revised copy with a reference to the original recap', () => {
    const { container } = mount(
      recap({ name: 'RECAP-0002', recap: 'RECAP-0002', status: 'queued', revision_of: 'RECAP-0001' }),
    )
    const marker = q(container, 'revision-of')
    expect(marker.textContent).toContain('Revised copy')
    expect(marker.textContent).toContain('RECAP-0001')
    expect(q(container, 'status').textContent).toBe('Queued')
  })

  it('keeps the original sent item labelled Sent with no revised marker', () => {
    const { container } = mount(recap({ can_send_revised: true }))
    expect(q(container, 'status').textContent).toBe('Sent')
    expect(q(container, 'revision-of')).toBeNull()
  })
})

describe('Activities.vue — coaching_recap branch', () => {
  const source = readFileSync(
    new URL('../../src/components/Activities/Activities.vue', import.meta.url),
    'utf8',
  )

  it('renders each coaching_recap item through CoachingRecapArea and reloads on @reload', () => {
    const branch = source.slice(
      source.indexOf("activity.activity_type == 'coaching_recap'"),
    )
    expect(branch).toMatch(
      /^activity\.activity_type == 'coaching_recap'"[\s\S]*?<CoachingRecapArea[\s\S]*?:activity="activity"[\s\S]*?@reload="all_activities\.reload\(\)"/,
    )
    expect(source).toContain(
      "import CoachingRecapArea from '@/components/Activities/CoachingRecapArea.vue'",
    )
  })
})
