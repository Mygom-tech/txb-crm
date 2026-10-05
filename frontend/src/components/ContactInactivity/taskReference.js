// Where a CRM Task's reference opens; an unknown doctype gets no route.
const REFERENCES = {
  Contact: { label: 'Contact', route: 'Contact', param: 'contactId' },
  'CRM Deal': { label: 'Deal', route: 'Deal', param: 'dealId' },
  'CRM Lead': { label: 'Lead', route: 'Lead', param: 'leadId' },
}

export function taskReference(doctype, docname) {
  const reference = REFERENCES[doctype]
  if (!reference || !docname) return { label: '', route: null }
  return {
    label: __(reference.label),
    route: { name: reference.route, params: { [reference.param]: docname } },
  }
}
