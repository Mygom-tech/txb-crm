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
| `Disqualified Reason Prompt`     | `Lead.vue` auto-open of `LostReasonModal` via `leadReasonPrompt.js`         |
| `Lead Creation Redirect`         | `router.js` tab-hash guard via `leadReasonPrompt.initialRouteTab`           |
| `Auto Refresh Call Count`        | `Deal.vue` reloads the deal after coaching actions / call-log changes        |
| `Notes Tab Rename`               | `notesTabLabel` in `dealPresentation.js`, wired into `Deal.vue` tabs         |
| `Hide Call Duration`             | `CallArea.vue` `hideDuration` option (`hideCallDuration`), set by `Deal.vue` |
| `Pipeline Section Visibility`    | committed pipeline `depends_on` (`pipelineLayout.js`) evaluated reactively by `SidePanelLayout.vue` |
| `Forecasting Script`             | server-derived probability (`CRM Deal.update_default_probability`), refreshed by `Deal.vue` after save |

The 15 Server Scripts are listed in `RETIRED_SERVER_SCRIPTS`; their logic is in
`crm/txb/doc_events`, `crm/txb/tasks` and `crm/txb/api`, wired through `hooks.py`.

`Generate Registration Token` is deliberately **not** here — `reissue_registration_tokens`
owns it.

## Still live, deliberately

`Workshop Datetime Modal`, `Organization Reload After Create`, the `Product Details` script
and the remaining `<style>` injectors. Nothing native replaces them yet. They remain a
standing risk: undiffable behaviour that can change between environments without a deploy.

`Disqualified Reason Prompt` and `Lead Creation Redirect` moved to **Retired** above
(TXB-146): an unresolved Disqualified lead now re-opens `LostReasonModal` natively through
`leadReasonPrompt.js`, and newly opened Lead routes default to the Data tab through the
`router.js` tab-hash guard.

`Auto Refresh Call Count`, `Notes Tab Rename` and `Hide Call Duration` moved to **Retired**
above (TXB-147): the deal's completed-call total now refreshes through the canonical
document reload after coaching actions and call-log changes, the Notes tab label is
computed for coaching deals, and call duration is hidden through an explicit Deal-scoped
`CallArea` option instead of a `<style>`/DOM strip.

`Pipeline Section Visibility` and `Forecasting Script` moved to **Retired** above (TXB-148).
Pipeline-specific field/section visibility is now committed `depends_on` in
`frontend/src/utils/pipelineLayout.js`, applied in `Deal.vue`'s `getParsedSections` and
evaluated reactively by `SidePanelLayout.vue` — including the correction of the script's
stale `pipeline_type == "Training"` condition to `"Selling Training"`, which had left
Selling Training deals ungated. Empty sections collapse through the standard
fields-layout rule already in `parsedSection`. The forecast probability is derived
server-side (`CRM Deal.update_default_probability`); `Deal.vue` reloads the document after a
status save so the server-derived value appears with no browser reload.

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
