"""Retire the `CRM Wizard Framework` Form Script.

All four pipelines now have their Take Action transitions in `crm/txb/pipelines/`, served
by `crm.txb.api.actions`. Until this row is disabled both implementations run: the Vue
Take Action menu and the script's injected one would appear together, and a user could
reach a transition through either.

What the script did, and why none of it is missed:

- Held the transition table -- from-states, target status, `adminOnly` -- as data in the
  database, unreviewable and undiffable. That table is now Python.
- Built its UI by injecting raw HTML through a MutationObserver.
- Wrote through several sequential REST calls per action, so a failure part-way could
  leave a deal half-updated. Actions now apply in a single save.
- Checked `adminOnly` against the literal `Administrator` account rather than a role, so
  the restriction protected nothing. Enforcement now lives in `validate()`.

`Add an Attendee (TBD)` is not ported: it carried `config: null` and rendered as a dead
menu entry.

Kept for the record. A patch runs once per site and could not stop the row coming back,
which it did — the retirement now re-runs on every migrate via
`crm.txb.retired_scripts`, which owns the list this file used to hold.
"""

from crm.txb.retired_scripts import retire_scripts


def execute():
	retire_scripts()
