"""A narrow Slack Web API adapter: one private DM to a workspace member found by email.

Only three methods are ever called -- users.lookupByEmail, conversations.open and
chat.postMessage -- which is exactly what the bot scopes users:read.email, im:write and
chat:write allow. Failures surface as `SlackError` carrying Slack's own error code (for example
`users_not_found`), never the token or the response headers.
"""

import requests

SLACK_API_URL = "https://slack.com/api/"
REQUEST_TIMEOUT_SECONDS = 10


class SlackError(Exception):
	"""A Slack call that did not succeed; `code` is safe to log and store."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


def _call(token: str, method: str, **params) -> dict:
	# Form-encoded rather than JSON: users.lookupByEmail does not accept a JSON body.
	try:
		response = requests.post(
			SLACK_API_URL + method,
			headers={"Authorization": f"Bearer {token}"},
			data=params,
			timeout=REQUEST_TIMEOUT_SECONDS,
		)
		payload = response.json()
	except requests.RequestException as e:
		raise SlackError(f"{method}: {type(e).__name__}") from None
	except ValueError:
		raise SlackError(f"{method}: invalid_response") from None

	if not payload.get("ok"):
		raise SlackError(f"{method}: {payload.get('error') or 'unknown_error'}")
	return payload


def send_direct_message(token: str, email: str, text: str) -> None:
	"""Send `text` as a one-to-one DM from the bot to the Slack member registered as `email`."""
	user_id = _call(token, "users.lookupByEmail", email=email)["user"]["id"]
	channel_id = _call(token, "conversations.open", users=user_id)["channel"]["id"]
	_call(token, "chat.postMessage", channel=channel_id, text=text)
