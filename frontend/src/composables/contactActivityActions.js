import { useDoctypeModal } from '@/composables/doctypeModal'
import { useTelemetry } from 'frappe-ui/frappe'

// The only two creation entry points on a Contact card (TXB-249): Log a Call and New Note.
// Both reuse the shared Lead CRM Call Log / FCRM Note doctype modals (same Quick Entry fields
// and validations), default the canonical reference to the Contact, and offer an optional
// Opportunity restricted to the Contact's linked CRM Deals. The aggregate Contact history
// stays read-only; it is reloaded only after a confirmed insert, so cancel or a validation
// failure leaves both persistence and the displayed history unchanged.
export function useContactActivityActions({
  contactId,
  contact,
  deals,
  activities,
}) {
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

  function logCall() {
    open('CRM Call Log', 'Call Log', 'call_log_created', {
      reference_doc: { ...(contact.doc || {}) },
    })
  }

  function newNote() {
    open('FCRM Note', 'Note', 'note_created')
  }

  return { logCall, newNote }
}
