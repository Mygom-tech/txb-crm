"""A narrow Slack Web API adapter: one private DM to a workspace member found by email.

Only three methods are ever called -- users.lookupByEmail, conversations.open and
chat.postMessage -- which is exactly what the bot scopes users:read.email, im:write and
chat:write allow. Failures surface as `SlackError` carrying Slack's own error code (for example
`users_not_found`), never the token, the response headers or the response body.

A definite answer from Slack (`ok: false`) is a rejection that may be retried. A
chat.postMessage that left without one -- a read timeout, a dropped connection, an unreadable
response -- may have been delivered all the same, so it is flagged `uncertain` and must not be
resent automatically.
"""

import requests
from urllib3.exceptions import NewConnectionError

SLACK_API_URL = "https://slack.com/api/"
REQUEST_TIMEOUT_SECONDS = 10


class SlackError(Exception):
	"""A Slack call that did not succeed; `code` is safe to log and store.

	`uncertain` means Slack may have accepted the message even though no success came back.
	"""

	def __init__(self, code: str, uncertain: bool = False):
		super().__init__(code)
		self.code = code
		self.uncertain = uncertain


def _never_reached_slack(error: requests.RequestException) -> bool:
	# No connection was ever made (connect timeout, refused, DNS), so nothing was dispatched.
	if isinstance(error, requests.ConnectTimeout):
		return True
	reason = getattr(error.args[0], "reason", None) if error.args else None
	return isinstance(reason, NewConnectionError)


def _call(token: str, method: str, delivers: bool = False, **params) -> dict:
	# Form-encoded rather than JSON: users.lookupByEmail does not accept a JSON body.
	# `delivers` marks the call that posts the message, whose lost answer is uncertain.
	try:
		response = requests.post(
			SLACK_API_URL + method,
			headers={"Authorization": f"Bearer {token}"},
			data=params,
			timeout=REQUEST_TIMEOUT_SECONDS,
		)
	except requests.RequestException as e:
		uncertain = delivers and not _never_reached_slack(e)
		raise SlackError(f"{method}: {type(e).__name__}", uncertain=uncertain) from None
	try:
		payload = response.json()
	except ValueError:
		raise SlackError(f"{method}: invalid_response", uncertain=delivers) from None

	if not payload.get("ok"):
		raise SlackError(f"{method}: {payload.get('error') or 'unknown_error'}")
	return payload


def send_direct_message(token: str, email: str, text: str) -> None:
	"""Send `text` as a one-to-one DM from the bot to the Slack member registered as `email`."""
	user_id = _call(token, "users.lookupByEmail", email=email)["user"]["id"]
	channel_id = _call(token, "conversations.open", users=user_id)["channel"]["id"]
	_call(token, "chat.postMessage", delivers=True, channel=channel_id, text=text)
