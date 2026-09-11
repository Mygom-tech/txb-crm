import { validateEmail } from '@/utils'

// Shown when a new Lead has neither a usable email nor a Mobile No. The exact
// wording is contractual: LeadModal.vue surfaces it verbatim and the CRM Lead
// backend guard throws the same sentence, so the modal and the API agree.
export const CONTACT_REQUIRED_MESSAGE =
  'Either email or phone number is required to create a Lead.'

// Shown when a supplied email is not a well-formed address.
export const INVALID_EMAIL_MESSAGE = 'Invalid email address'

// Email is optional on its own, but a supplied value must be a valid address.
// Trims first so a whitespace-only value counts as absent. Returns an error
// message string, or null when the value is acceptable.
export function validateLeadEmail(email) {
  const trimmed = (email || '').trim()
  if (!trimmed) return null
  return validateEmail(trimmed) ? null : INVALID_EMAIL_MESSAGE
}

// Shared new-Lead contactability rule, mirroring the CRM Lead backend guard: a
// trimmed email OR a trimmed mobile_no must be present, and a supplied email
// must be well-formed. The separate `phone` field deliberately does not count.
// The email check runs first so a malformed address blocks creation even when a
// Mobile No. is present. Returns an error message string, or null when valid.
export function validateLeadContact({ email, mobile_no } = {}) {
  const emailError = validateLeadEmail(email)
  if (emailError) return emailError

  const hasEmail = Boolean((email || '').trim())
  const hasMobile = Boolean((mobile_no || '').trim())
  if (!hasEmail && !hasMobile) return CONTACT_REQUIRED_MESSAGE

  return null
}
