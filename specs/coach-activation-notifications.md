# Coach activation notifications

When a Delivering Coaching client is moved to **Active** for the first time (the **Set First
Call Date** action), `crm.txb.delivery_coach_assignment.record_activation`:

| Who | What |
| --- | --- |
| Delivery Coach | Email "New client assigned: {client}" — name, organization, email, phone, program, status, and "reach out within the next 48 hours" |
| Delivery Coach | CRM Task "Reach out to the client - {organization}", High, due in 48 hours |
| Client (primary Contact's email) | Lithuanian email "Jums paskirtas koučeris" — coach's name, program, contact within 48 hours |
| Owner of the sales Opportunity it was handed over from | In-CRM notification "{client} is now coached by {coach}" |

It runs once per deal (ledger key `<deal>-activation`); Reactivate does not repeat it. A failure is
logged and never blocks the activation.

**Coach change after activation:** a new coach on an Active deal gets the coach email only; the
client is not emailed again. Before activation a coach change sends nothing.

## Delivery

- Ledger `CRM Coach Assignment Notification`, channels `email` (coach) and `client_email`, each
  Pending → Sent / Failed / Skipped independently, retried by the TXB-290 sweep and System Manager
  retry.
- Mode on FCRM Settings: **Disabled** sends no email (the task and the CRM notification still
  happen), **Test redirect** sends both emails to the Test Recipient with a `[TEST for …]` subject,
  **Live** sends to the coach and the client.
- A client with no valid primary email fails only `client_email`.

## Templates (editable Email Templates, TXB-116 wrapper)

- `TxB Delivery Coach Assignment` — re-seeded on migrate only while unedited.
- `TxB Coach Assigned to Client` — seeded once.

## Slack

Removed: the Slack DM channel, the `slack_bot_token` setting (patch
`coach_activation_notifications` deletes the field and its stored secret). Ledger rows recorded
before the client channel existed get `client_email` Skipped, so no past event emails a client.
