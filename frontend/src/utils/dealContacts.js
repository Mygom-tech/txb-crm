// Transform for the CRM Deal "Contacts" section resource.
//
// Every Contact card starts expanded when contacts load so the key contact
// details (email, phone) are visible by default. The per-card chevron still
// toggles `opened` afterwards, so this only sets the initial state and never
// forces a card to stay open during the view.
export function expandDealContacts(data) {
  if (!Array.isArray(data)) return data
  data.forEach((contact) => {
    contact.opened = true
  })
  return data
}
