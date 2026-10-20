# Manual call logging

Coaches and sales call from their phones and log the call in the CRM. Every manual call goes
through one server-checked contract (`crm/txb/call_logging.py`, browser copy in
`frontend/src/utils/leadActions.js`).

## Where

| Surface | Record | Extra |
| --- | --- | --- |
| Log a dial | Lead | Moves the Lead to Contact attempted (`crm.txb.lead_actions.log_a_dial`) |
| Log a call | Lead, Deal (activity header), Contact | Contact offers an optional Opportunity (`crm.txb.call_logging.log_a_call`) |
| Log Coaching Call | Delivering Coaching Deal | Unchanged: Completed / Missed / No charge, "Coaching Call Notes" |

## Fields

| Field | Rule |
| --- | --- |
| Dialed At | required, defaults to now |
| Result | No Answer · Completed · In Progress · Other |
| Call Notes | required |
| Next Call Date | required when Result is **In Progress** (not reached, retry planned) |

A call writes an outgoing manual CRM Call Log with the result, a Note with the Call Notes linked
to the record, and — when a Next Call Date is given — a "Follow up on call" CRM Task assigned to
whoever logged it.

## Counting

- Completed is the only result any counter treats as reached (human contact, completed calls).
- In Progress counts as an attempt, not a reach.
- Other is stored but counts nowhere.
- Older logs keep their stored status (Busy, Failed, Canceled…); only new logs use the four results.
