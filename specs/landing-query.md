# Landing Query

`custom_landing_query` (Small Text, read-only) on **CRM Lead** and **CRM Deal** stores the raw
query string of the public page a record registered from, e.g. `?utm_campaign=workshop_2026`.
Filterable in list views (`like`), exportable.

## Writers
- **External API (TxB landing page)**: sends `custom_landing_query: location.search` in the
  `POST /api/resource/CRM Lead` body. No CRM code involved.
- **CRM web forms (`/crm-form/<route>`) and workshop registration (`/register`)**: the shared
  `crm/www/crm_form.html` posts `landing_query`; `crm.api.form._apply_landing_query` stores it
  (falls back to the Referer's query), capped at 1000 chars, never overwriting a set value.
- **Lead → Deal conversion**: copied automatically (same fieldname).

## Notes
- Installed by `crm.patches.v1_0.add_landing_query_field`.
- No index: TEXT column + `LIKE '%…%'` can't use one. Add a generated `utm_campaign` column if
  filtering gets slow.
- GDPR: may contain click IDs (gclid/fbclid); covered by the form's consent/privacy notice.
