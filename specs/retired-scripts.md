# Retiring database scripts, and why the old Take Action came back

## Symptom

Deal pages showed **two** Take Action menus: the native one
(`crm.txb.api.actions.get_available_actions`) and the one `CRM Wizard Framework` injects
through `document._actions`. A user could reach a transition through either, and the
script's copy enforces nothing server-side.

## Why disabling it once was not enough

Each retirement shipped as a `crm.patches.v1_0.disable_*` patch. A Frappe patch runs
**once per site, forever** — its name is recorded in `tabPatch Log` and migrate never
looks at it again. Nothing then holds the row closed:

- someone flips `enabled` back in the desk UI;
- a site is restored from a dump taken before the patch landed;
- an environment is rebuilt from a backup whose Patch Log already lists the patch, so
  migrate skips it while the row it was meant to disable is on.

Scripts live as database rows, so none of that shows up in a diff.

## The fix

`crm/txb/retired_scripts.py` owns one list of retired Form Scripts (keyed on `enabled`)
and Server Scripts (keyed on `disabled`), and is wired into `after_migrate` in `hooks.py`.
It re-asserts the retirement on **every** `bench migrate` — the one command every deploy
already runs — so production picks it up with no extra step. Bringing a script back is now
a code change (removing its name from the tuple), not an undetected click.

`_disable` catches and logs per row: a failure here must never abort a migrate.

The five patch files stay for their write-ups, but delegate to `retire_scripts()` instead
of holding their own copies of the list. `disable_migrated_server_scripts` keeps
`repoint_registration_page()` — that one genuinely is a one-off.

## Retired

| Form Script                      | Replaced by                                                                  |
| -------------------------------- | ---------------------------------------------------------------------------- |
| `CRM Wizard Framework`           | `crm/txb/pipelines/` + `crm.txb.api.actions` — **the duplicate Take Action** |
| `Convert Dialog - Pipeline Type` | `ConvertToDealModal.vue`                                                     |
| `Pipeline Status Filter`         | `crm.txb.api.pipelines.get_pipeline_statuses`                                |
| `Lead Owner Read-Only`           | `restrict_owner_field` / `guard_owner_change`                                |
| `Contact_Create Opportunity`     | `CreateDealFromContactModal.vue`                                             |

The 15 Server Scripts are listed in `RETIRED_SERVER_SCRIPTS`; their logic is in
`crm/txb/doc_events`, `crm/txb/tasks` and `crm/txb/api`, wired through `hooks.py`.

`Generate Registration Token` is deliberately **not** here — `reissue_registration_tokens`
owns it.

## Still live, deliberately

`Auto Refresh Call Count`, `Disqualified Reason Prompt`, `Lead Creation Redirect`,
`Notes Tab Rename`, `Pipeline Section Visibility`, `Workshop Datetime Modal`,
`Organization Reload After Create`, the `Product Details` / `Forecasting` scripts and the
three `<style>` injectors. Nothing native replaces them yet. They remain a standing risk:
undiffable behaviour that can change between environments without a deploy.

## Verifying

```bash
bench --site <site> run-tests --module crm.txb.test_retired_scripts   # 4 tests
```

In production, after the deploy's `bench --site all migrate`:

```bash
bench --site <site> execute frappe.client.get_list \
  --kwargs "{'doctype':'CRM Form Script','filters':{'enabled':1},'pluck':'name'}"
```

`CRM Wizard Framework` must not appear. Then open any deal: exactly one Take Action button.
