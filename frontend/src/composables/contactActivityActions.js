import { useDoctypeModal } from '@/composables/doctypeModal'
import { useTelemetry } from 'frappe-ui/frappe'
import { logACall } from '@/utils/leadActions'

// The only two creation entry points on a Contact card (TXB-249): Log a Call and New Note.
// Log a Call opens the shared server-checked call dialog; New Note reuses the FCRM Note
// doctype modal. Both reference the Contact and offer an optional Opportunity restricted to
// the Contact's linked CRM Deals. The aggregate Contact history
// stays read-only; it is reloaded only after a confirmed insert, so cancel or a validation
// failure leaves both persistence and the displayed history unchanged.
export function useContactActivityActions({ contactId, deals, activities }) {
  const { showModal } = useDoctypeModal()
  const { capture } = useTelemetry()

  function opportunityOptions() {
    return (deals.data || []).map((deal) => ({
      label: deal.organization
        ? `${deal.name} · ${deal.organization}`
        : deal.name,
      value: deal.name,
    }))
  }

  function open(doctype, title, event, extraDefaults = {}) {
    showModal({
      doctype,
      title,
      defaults: {
        reference_doctype: 'Contact',
        reference_docname: contactId,
        opportunity: '',
        ...extraDefaults,
      },
      opportunities: opportunityOptions(),
      callbacks: {
        afterInsert: (d) => {
          capture(event)
          activities.reload()
          // A linked Opportunity's deal list row (e.g. completed call count) may change.
          if (d?.opportunity) deals.reload()
        },
      },
    })
  }

  async function logCall() {
    const logged = await logACall('Contact', contactId, {
      opportunities: opportunityOptions(),
    })
    if (!logged) return
    capture('call_log_created')
    activities.reload()
    // A linked Opportunity's deal list row (e.g. completed call count) may change.
    deals.reload()
  }

  function newNote() {
    open('FCRM Note', 'Note', 'note_created')
  }

  return { logCall, newNote }
}
