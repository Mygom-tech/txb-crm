# Email shell

Every email the site sends — CRM, TxB and Frappe's own (password reset, welcome, login link) —
goes out in one TXB-branded shell: `crm/templates/emails/standard.html`. It overrides Frappe's
`templates/emails/standard.html` because crm is searched before frappe by the Jinja loader, so
no send call changes.

- **Look** (design option C, Editorial White): all-white, no card. Logo top-left, content,
  then a 1 px `#cccccc` rule over a two-cell footer in `#4d4d4d`
  ("Ten Times Better Consulting" left, "txbconsulting.com" right).
- **Email-safe**: tables and inline styles only, 600 px wide; the logo is the brand-hosted PNG
  `https://assets.txbconsulting.com/logo.png` (175×40, shown at 131×30) since Gmail and Outlook
  do not render SVG. The old `/assets/crm/images/txb-email-logo.png` stays: already-sent emails
  still load it. Fonts fall back to Helvetica/Arial: Roobert never loads in mail clients.
- **Kept from Frappe**: `header` (notification title + indicator), `print_html` and its footer
  (email-account footer, System Settings "Email Footer Address", unsubscribe slot, open-tracking
  pixel) under the brand line. Only the "Sent via Frappe" line is off
  (`disable_standard_email_footer`, set on install and by the patch).
- **Content only**: Email Templates hold content, not branding. Patch `unwrap_email_templates`
  strips the TXB-116 header and footer from the confirmation and both coach-assignment
  templates, keeping the greeting-to-signature container.

Legal address: set it in System Settings → Email Footer Address; it shows in every footer.
