# Cross-object people search (TXB-112)

## Why

The Leads list search is bound to the current View, its filters and one doctype. A
person already in the CRM as a **Contact** is therefore invisible while the user types
a whole new **Lead** — and the only thing that tells them is `prevent_duplicate`
(TXB-73), which fires _after_ Create. The user loses the form they just filled in and
learns nothing about where the existing record is.

This feature answers "does this person already exist, anywhere?" while they type.

## What it blocks

An **exact** match — same email, or the same phone after normalization — disables the
Create button in both modals and explains why. A name-only match never blocks anything.

The block is **frontend-only, by design**. TXB-73 remains the actual boundary, so the
Facebook lead sync (`crm/lead_syncing`), the guest registration endpoint and bulk imports
still create records the UI would refuse. Making it unbypassable means changing what the
API accepts, which would start throwing in those non-UI paths.

Known false positive: B2B companies that publish one `info@` address and one switchboard
number. A second person there cannot be created until their email or phone is changed.
This was a deliberate call — accepted in exchange for catching the shared-address
duplicates that motivated the ticket.

## Pieces

| File                                              | Role                                                                                                                                                |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `crm/txb/people.py`                               | Shared matching primitives. `find_exact_duplicate` is TXB-73's rule, moved here verbatim; `normalize_name/email/phone` are used by the search only. |
| `crm/txb/api/people_search.py`                    | `search_people(name, email, phone, limit)` — whitelisted endpoint searching CRM Lead + Contact in one call.                                         |
| `crm/txb/doc_events/lead.py`                      | `prevent_duplicate` now delegates to `find_exact_duplicate`. Behaviour unchanged.                                                                   |
| `frontend/src/components/DuplicatePersonHint.vue` | Debounced panel rendered inside both create modals.                                                                                                 |
| `crm/txb/test_people_search.py`                   | 16 tests: normalization, both create directions, hidden status, ranking.                                                                            |

The two create modals (`LeadModal.vue`, `ContactModal.vue`) each render the hint **above**
`FieldLayout`, fed by the `first_name` / `last_name` / `email` / `mobile_no` the user is
already typing. No extra search box — the AC requires results _before_ Create is
pressed, and a second input users must remember to fill would not deliver that.

The result list is capped at `max-h-52` and scrolls internally, so a broad query cannot
push the form off screen. The panel turns red on an exact match, and reports its state to
the parent via `v-model:blocked`.

## Matching rules

- **Name** — tokenised (max 3 tokens, min 3 chars each) and ORed across `first_name`,
  `last_name` and, for Leads, `lead_name`. Contact has no full-name column, which is
  why tokenising is required rather than a single LIKE.
- **Email** — case-insensitive equality. A hit is an **exact** match.
- **Phone** — compared on the trailing 8 digits after stripping non-digits, so
  `+370 612 34567`, `861234567` and `(8-612) 34-567` are one number. A hit is **exact**.
- **Status is never filtered.** Disqualified and converted Leads are returned; a hidden
  status must not be able to hide a duplicate.
- Results are ranked exact-first, then by how many name tokens the record accounts for,
  then alphabetically. The token count matters: without it, a full-name match sits below
  every namesake and falls off the end of `limit` — the original bug, one layer up.

## Permissions and the `restricted` count

`CRM Lead` carries an org-hierarchy `permission_query_conditions`, so a permission-aware
search cannot see leads outside the caller's hierarchy — exactly the case where they are
most likely to create a duplicate.

The endpoint therefore runs two passes: a candidate pass **without** permission filtering
to learn that a match exists, then `frappe.get_list` to return only what the caller may
read. The difference is reported as `restricted` — a bare integer, no names, owners or
emails. The UI renders "N more matching record(s) exist but are not visible to you."

This discloses strictly less than TXB-73's existing error message, which already prints
the hidden person's full name and email at Create time.

## Known limits

- The phone leg strips non-digits in SQL (`REGEXP_REPLACE`), so it cannot use an index.
  Acceptable at Lead/Contact volumes and bounded by `CANDIDATE_CAP` (200 rows/doctype).
  A stored normalized-phone column is the fix if the tables grow an order of magnitude.
- `first_name` / `last_name` / `mobile_no` have no `search_index` on either doctype; the
  name leg is a leading-wildcard LIKE and would not use one anyway.
- A search failure is logged and swallowed — it must never block the create flow it is
  assisting.

## Verifying

```bash
bench --site <site> run-tests --module crm.txb.test_people_search   # 16 tests
bench --site <site> run-tests --module crm.txb.test_doc_events      # TXB-73 unchanged
```

Manually, the scenario from the ticket: with a Contact that the Leads View does not show,
open **Create Lead**, type that person's name — the panel must surface the Contact,
labelled as one, with an **Open** button that navigates to it.
