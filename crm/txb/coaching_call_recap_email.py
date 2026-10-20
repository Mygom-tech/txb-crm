"""The client-facing Coaching Call recap email, rendered from a recap's content snapshot (TXB-274).

Only the snapshot is read, so the email carries exactly what the coach submitted even if the
Note or Deal changed since. Every coach-written value is HTML-escaped and the body is plain
formatted text: no links, images or attachments. Escaping only touches markup characters, so
Lithuanian letters (ąčęėįšųūž) reach the client unchanged.
"""

import html

from frappe import _
from frappe.utils import escape_html

LINE_BREAK = "<br>"


def render_recap_email(snapshot: dict) -> tuple[str, str]:
	"""The `(subject, html)` of one recap."""
	# A header, not HTML: collapsed to one line so coach text can never add a header.
	subject = " ".join(str(snapshot.get("subject") or _("Coaching call recap")).split())

	# Order is the product's recap structure: intro, when, summary, action items, next call.
	parts = [f"<p>{_escape(_('Here is a recap of your coaching call.'))}</p>"]
	if snapshot.get("delivery_date"):
		parts.append(f"<p>{_escape(_('Date'))}: {_escape(snapshot['delivery_date'])}</p>")
	for label, key in ((_("Summary"), "call_notes_html"), (_("Action items"), "action_items_html")):
		text = _notes_html(snapshot.get(key))
		if text:
			parts.append(f"<p><strong>{_escape(label)}</strong><br>{text}</p>")
	if snapshot.get("is_last_call"):
		parts.append(f"<p>{_escape(_('This was your last coaching call.'))}</p>")
	elif snapshot.get("next_call_date"):
		parts.append(f"<p>{_escape(_('Next call'))}: {_escape(snapshot['next_call_date'])}</p>")
	return subject, "\n".join(parts)


def _escape(value) -> str:
	return escape_html(str(value))


def _notes_html(notes_html: str | None) -> str:
	"""The snapshot's notes, escaped exactly once whatever the stored form.

	The snapshot stores them already escaped with `<br>` line breaks; each line is unescaped one
	level and escaped again, so stored text reads as written and any raw markup that slipped
	into a snapshot is still shown as text rather than rendered.
	"""
	lines = (notes_html or "").split(LINE_BREAK)
	return LINE_BREAK.join(_escape(html.unescape(line)) for line in lines).strip()
