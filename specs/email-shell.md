# Email shell

Every email the site sends — CRM, TxB and Frappe's own (password reset, welcome, login link) —
goes out in one TXB-branded shell: `crm/templates/emails/standard.html`. It overrides Frappe's
`templates/emails/standard.html` because crm is searched before frappe by the Jinja loader, so
no send call changes.

- **Look** (design option B): blue `#1131f2` header with the white logo, a 4 px `#ff3c00` rule,
  white content card on `#f2f2f2`, gray `#cccccc` footer in Lithuanian
  ("Ten Times Better Consulting · txbconsulting.com").
- **Email-safe**: tables and inline styles only, 600 px wide; the logo is a hosted 2× PNG
  (`/assets/crm/images/txb-email-logo.png`) since Gmail and Outlook do not render SVG. Fonts fall
  back to Helvetica/Arial: Roobert never loads in mail clients. Outlook squares the corners.
- **Kept from Frappe**: `header` (notification title + indicator), `print_html` and its footer
  (email-account footer, System Settings "Email Footer Address", unsubscribe slot, open-tracking
  pixel) under the brand line. Only the "Sent via Frappe" line is off
  (`disable_standard_email_footer`, set on install and by the patch).
- **Content only**: Email Templates hold content, not branding. Patch `unwrap_email_templates`
  strips the TXB-116 header and footer from the confirmation and both coach-assignment
  templates, keeping the greeting-to-signature container.

Legal address: set it in System Settings → Email Footer Address; it shows in every footer.
