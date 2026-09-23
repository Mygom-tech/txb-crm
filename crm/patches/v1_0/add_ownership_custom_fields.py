"""Install the Claim Request fields.

Kept separate from the flow itself so the fields exist before any code reads them. No
person is seeded here: who receives Admin tasks is a setting an administrator fills in
(see `crm.txb.admin_assignment`), and an empty setting resolves to the longest-standing
Admin rather than to anyone named in source (TXB-263).
"""

from crm.install import add_ownership_custom_fields


def execute():
	add_ownership_custom_fields()
