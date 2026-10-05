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

	rows = [
		(_("Date"), snapshot.get("delivery_date")),
		(_("Call status"), snapshot.get("call_status")),
		(_("Topic"), snapshot.get("topic")),
	]
	parts = [f"<p><strong>{_escape(subject)}</strong></p>"]
	parts += [f"<p>{_escape(label)}: {_escape(value)}</p>" for label, value in rows if value]
	notes = _notes_html(snapshot.get("call_notes_html"))
	if notes:
		parts.append(f"<p>{notes}</p>")
	if snapshot.get("next_call_date") and not snapshot.get("is_last_call"):
		parts.append(f"<p>{_escape(_('Next call'))}: {_escape(snapshot['next_call_date'])}</p>")
	if snapshot.get("is_last_call"):
		parts.append(f"<p>{_escape(_('This was your last coaching call.'))}</p>")
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
