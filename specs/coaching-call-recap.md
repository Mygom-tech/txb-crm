# Log Coaching Call — Action Items & Client Recap Email

## Action Items

- `action_items` (Small Text, **required**) on the Log Coaching Call form, after Coaching Call Notes.
- Required is enforced server-side by `validate_required` in `crm/txb/api/actions.py`, so blank or
  whitespace-only values are rejected for direct API calls too.
- Written into the Coaching Call Note body as an `Action Items:` block, and into the recap
  snapshot as `action_items_html` (escaped, `<br>` line breaks).

## Recap email structure

`render_recap_email` (`crm/txb/coaching_call_recap_email.py`) renders, in order:

1. Intro: "Here is a recap of your coaching call."
2. Date: the Coaching Call Delivery Date
3. **Summary**: the Coaching Call Notes
4. **Action items**
5. Next call date, or "This was your last coaching call." when it was the last call

The body no longer shows Call Status or Topic, and no longer repeats the subject. Topic is still
in the subject line (taken from the Note title).

Recaps queued before this change have no `action_items_html`, so they are sent without that section.
