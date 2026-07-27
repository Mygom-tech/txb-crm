# Kanban transition confirm

Status: implemented (2026-07-27)
Date: 2026-07-27
Branch: `feature/kanban-transition-confirm`

## Context

When a kanban card is dragged to another column, the change saves immediately with no
confirmation — and fire-and-forget: `ViewControls.vue` calls `frappe.client.set_value`
with no `.then`/`.catch`. Server-side validation can reject the write (e.g. moving a
deal to a "Lost" status without `lost_reason` throws in `crm_deal.py:284-288`), leaving
the card visually moved while the server kept the old value. The card's in-memory badge
also goes stale after a move, and cross-column drops discard card ordering
(`kanban_columns` is never persisted on that branch).

This feature adds a Cancel/OK confirmation before persisting a cross-column move, and is
the first step toward configurable transition rules. Along the way it fixes the
silent-failure, stale-badge, and discarded-ordering bugs, because the confirm flow has to
own exactly those code paths.

## Decisions (user-confirmed)

- Purpose: foundation for future transition rules (allow/block, validations), confirm
  modal is the first consumer.
- Scope: fires on **every** cross-column kanban drag, regardless of which Link/Select
  field the board is grouped by — not just status. Applies to all kanban consumers
  (Leads, Deals, Tasks).
- Persistence: **confirm before saving.** OK persists; Cancel reverts the card locally
  with no server call. Same-column reorders are untouched.
- Ownership (post Devil's-Advocate review): confirm + revert live in
  `ViewControls.updateKanbanSettings` — the same place as the save — so Cancel and
  server-error share one revert mechanism. No global check registry.

## Architecture

```
KanbanView.vue (drag ends; vuedraggable already moved the card optimistically)
  └─ moveCardBetweenColumns(evt)          [extracted from updateColumn, stays sync]
       └─ emit('update', { item, to, from, oldIndex, kanban_columns })
            └─ ViewControls.updateKanbanSettings(data)   [async]
                 ├─ await requestKanbanTransition(ctx)
                 │    ├─ false → revertCardMove(...)                    // Cancel
                 │    └─ true  → call set_value
                 │           ├─ success → card[column_field] = to       // badge fix
                 │           │            + persist kanban_columns      // ordering fix
                 │           └─ failure → revertCardMove(...) + toast.error
```

## Components

### `frontend/src/utils/kanbanTransitions.js` (new)

- `requestKanbanTransition(ctx) → Promise<boolean>` — the extension seam. Today its body
  is only `confirmKanbanTransition(ctx)`. Future rules are added inside this module and
  run **before** the confirm (cheap checks must not run after the user already confirmed).
  No global mutable registry: avoids Vite-HMR duplicate registration and vitest module
  state pollution.
- `confirmKanbanTransition(ctx) → Promise<boolean>` — wraps `createDialog` from
  `utils/dialogs.jsx`. OK resolves `true`; Cancel, Esc, X, and outside-click resolve
  `false`. The promise resolves **exactly once** (guard flag) — a dismissal that never
  resolves would hang the flow.
- `ctx = { doctype, itemName, fieldname, fieldLabel, from, to }`. `fieldLabel` is
  resolved by the caller (`ViewControls`) from the doctype meta (`stores/meta.js`
  `getFields()`), falling back to `fieldname`. Message:
  `__('Change {0} from "{1}" to "{2}"?', [fieldLabel, from, to])` with a generic title.
  All strings through `__()`.

### `frontend/src/utils/kanbanRevert.js` (new, pure)

- `revertCardMove({ columns, itemName, from, to, oldIndex }) → boolean` — identity-based,
  never trusts stale references: re-resolves source/target columns **by name** in the live
  `columns` array, finds the card by `row.name === itemName`, clamps the reinsert index to
  the source array length. Returns `false` if the card or either column can't be found
  (caller falls back to `list.reload()`).
- Why index-free: `loadMoreKanban` and the view-change watcher call `list.reload()`,
  which replaces the column arrays wholesale while the modal is open; splicing captured
  references would mutate garbage. `evt.oldIndex` is a DOM index and only coincidentally
  matches the VM index.
- This is the primary unit-test target.

### `frontend/src/components/Kanban/KanbanView.vue`

- Extract the cross-column branch of `updateColumn` (lines ~256-277) into
  `moveCardBetweenColumns(evt)`. `updateColumn` keeps its six existing call sites and
  stays synchronous (one caller passes a MouseEvent; nothing async may depend on it).
- The emitted payload gains `from` and `oldIndex`.

### `frontend/src/components/ViewControls.vue`

- `updateKanbanSettings` item-branch becomes async: pipeline → revert or save as in the
  architecture diagram. On success, also persist `kanban_columns` (currently discarded on
  cross-column moves — drop position is lost on reload today) and set the moved card's
  `column_field` value in memory so the badge updates.
- Error toast via the app's existing toast util on save failure, after the revert.

### `frontend/src/utils/dialogs.jsx`

- Fix properly (it's the `$dialog` used by form scripts too): remove dialog entries from
  `dialogs.value` on close (today they accumulate forever), pass `key={dialog.key}` to the
  vnode (assigned but unused today — required once entries are removed), and support an
  `onDismiss`/close callback so Esc/X/outside-click can be observed by callers.

## Error handling

- Save failure → revert + `toast.error` with the server message. No retry machinery.
- `revertCardMove` returning `false` (board changed under the modal) → `list.reload()`.
- The modal is pure UX, **not** an authorization control. Enforced transition rules, when
  they come, belong in server-side `validate()` (`crm_deal.py` et al.), with this module
  only mirroring them client-side.

## Testing

- Unit (`frontend/tests/unit/`, vitest + happy-dom): `revertCardMove` — happy path,
  card-not-found, source-column-gone, index clamping. `requestKanbanTransition` with a
  mocked confirm. Add new files to the coverage `include` list in `vitest.config.js`.
- Manual (dev server, all three kanban consumers): drag + OK persists and badge updates;
  drag + Cancel/Esc/X/outside-click reverts with no server write; drag a deal to a Lost
  status without `lost_reason` → server rejects → card reverts + toast; Load More while
  the modal is open, then Cancel → board reloads cleanly; column reorder and same-column
  reorder show no modal.
- Existing suite (`yarn test:run`, 129 tests) stays green.

## Out of scope

- Confirmation on non-kanban status changes (detail pages, modals, bulk actions).
- Per-doctype/per-field configurability of the confirm; rule engine; form-script hooks
  (the seam exists, the consumers don't).
- Per-card in-flight guard against racing drags (pre-existing, rare; failure is now
  observable rather than silent).
- Telemetry on transitions.

## Devil's-Advocate findings incorporated

Revert ownership moved next to the save; identity-based revert instead of index splicing;
registry dropped for a single-module seam; confirm runs last, rules-before-confirm
ordering documented; `dialogs.jsx` leak/keyless-render fixed rather than adopted as-is;
`updateColumn` kept sync with the async path extracted; `kanban_columns` ordering
persisted on cross-column moves.
