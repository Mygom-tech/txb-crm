# Kanban Transition Confirm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel/OK confirmation before a kanban cross-column drag persists, with one identity-based revert used by both Cancel and server-error, fixing the silent-failure / stale-badge / discarded-ordering bugs on the way.

**Architecture:** `KanbanView.vue` emits a richer `update` payload (`item, to, from, oldIndex, kanban_columns`) and stays synchronous. `ViewControls.updateKanbanSettings` owns the whole transition: awaits `requestKanbanTransition()` (today: just the confirm dialog — the seam for future rules), reverts on Cancel, saves on OK, reverts+toasts on save failure. The revert is a pure, unit-tested function that re-resolves everything by identity.

**Tech Stack:** Vue 3 SFC + `<script setup>`, vuedraggable, frappe-ui (local submodule), vitest + happy-dom.

**Spec:** `specs/kanban-transition-confirm.md` (approved). All work on branch `feature/kanban-transition-confirm`.

## Global Constraints

- Repo root for all paths/commands: `/home/vainius/projects/modiggo/projects/mygom/txb/txb-crm-be/apps/crm` (frontend commands run in `frontend/`).
- All user-facing strings through `__('...')` (global translation fn; available in utils without import; the test setup shims it).
- Do NOT touch `frontend/src/pages/Leads.vue`, `frontend/src/pages/Dashboard.vue`, `README.md`, or either `yarn.lock` — they hold the user's uncommitted work. Stage files explicitly (`git add <paths>`), never `git add -A`.
- Commit message style: lowercase conventional prefix (`feat:`, `fix:`, `test:`, `chore:`), ending with the two Claude trailers used in commit `30c90fe` (Co-Authored-By + Claude-Session).
- After each task: `yarn test:run` in `frontend/` must be green (129 pre-existing tests + new ones).
- Final pass runs prettier + eslint on touched files only (repo has no lint script wired into CI; don't reformat unrelated files).
- No `console.log` debugging left behind. No new dependencies.

---

### Task 1: `revertCardMove` pure util

**Files:**
- Create: `frontend/src/utils/kanbanRevert.js`
- Test: `frontend/tests/unit/kanbanRevert.test.js`
- Modify: `frontend/vitest.config.js` (coverage include list)

**Interfaces:**
- Consumes: nothing.
- Produces: `revertCardMove({ columns, itemName, from, to, oldIndex }) → boolean` — `columns` is the live kanban array (shape `[{ column: { name }, data: [rows] }]`, i.e. `list.value.data.data` in ViewControls). Returns `false` when the board changed underneath (caller must `list.reload()`). Task 5 imports this exact name from `@/utils/kanbanRevert`.

- [ ] **Step 1: Write the failing test**

`frontend/tests/unit/kanbanRevert.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { revertCardMove } from '@/utils/kanbanRevert'

function makeColumns() {
  return [
    {
      column: { name: 'Open' },
      data: [{ name: 'CRM-LEAD-01' }, { name: 'CRM-LEAD-02' }],
    },
    {
      column: { name: 'Contacted' },
      data: [{ name: 'CRM-LEAD-03' }, { name: 'CRM-LEAD-04' }],
    },
  ]
}

describe('revertCardMove', () => {
  it('moves the card back to the source column at oldIndex', () => {
    // simulate: CRM-LEAD-02 was dragged from Open (index 1) to Contacted
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 1,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-01',
      'CRM-LEAD-02',
    ])
    expect(columns[1].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-03',
      'CRM-LEAD-04',
    ])
  })

  it('clamps oldIndex when the source column shrank', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])
    columns[0].data.length = 0 // source emptied while modal was open

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 5,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual(['CRM-LEAD-02'])
  })

  it('defaults to end of source column when oldIndex is undefined', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: undefined,
    })

    expect(ok).toBe(true)
    expect(columns[0].data.map((r) => r.name)).toEqual([
      'CRM-LEAD-01',
      'CRM-LEAD-02',
    ])
  })

  it('returns false when the card is not in the target column', () => {
    const columns = makeColumns()
    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-99',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 0,
    })
    expect(ok).toBe(false)
  })

  it('returns false when the source column is gone', () => {
    const columns = makeColumns()
    columns[1].data.push(columns[0].data.splice(1, 1)[0])
    columns.splice(0, 1)

    const ok = revertCardMove({
      columns,
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 1,
    })
    expect(ok).toBe(false)
  })

  it('returns false when the target column is gone', () => {
    const ok = revertCardMove({
      columns: [{ column: { name: 'Open' }, data: [] }],
      itemName: 'CRM-LEAD-02',
      from: 'Open',
      to: 'Contacted',
      oldIndex: 0,
    })
    expect(ok).toBe(false)
  })

  it('returns false for empty/absent columns array', () => {
    expect(
      revertCardMove({
        columns: [],
        itemName: 'X',
        from: 'A',
        to: 'B',
        oldIndex: 0,
      }),
    ).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `frontend/`): `yarn vitest run tests/unit/kanbanRevert.test.js`
Expected: FAIL — cannot resolve `@/utils/kanbanRevert`.

- [ ] **Step 3: Write minimal implementation**

`frontend/src/utils/kanbanRevert.js`:

```js
/**
 * Moves a kanban card back from `to` column to `from` column after a
 * cancelled or failed transition. Identity-based on purpose: while the
 * confirm modal was open, `list.reload()` (Load More, view switch) may have
 * replaced the column arrays wholesale, so captured references and DOM
 * indices from the drag event cannot be trusted.
 *
 * @param {Object} args
 * @param {Array} args.columns - live kanban columns: [{ column: { name }, data: [rows] }]
 * @param {string} args.itemName - row `name` of the moved card
 * @param {string} args.from - source column name
 * @param {string} args.to - target column name
 * @param {number} [args.oldIndex] - original position in the source column
 * @returns {boolean} false when the board changed underneath and the caller
 *   should fall back to a reload
 */
export function revertCardMove({ columns, itemName, from, to, oldIndex }) {
  if (!Array.isArray(columns) || !columns.length) return false

  const sourceColumn = columns.find((col) => col.column?.name == from)
  const targetColumn = columns.find((col) => col.column?.name == to)
  if (!sourceColumn?.data || !targetColumn?.data) return false

  const cardIndex = targetColumn.data.findIndex((row) => row.name == itemName)
  if (cardIndex === -1) return false

  const [card] = targetColumn.data.splice(cardIndex, 1)
  const insertAt = Math.min(
    typeof oldIndex === 'number' ? oldIndex : sourceColumn.data.length,
    sourceColumn.data.length,
  )
  sourceColumn.data.splice(insertAt, 0, card)
  return true
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `yarn vitest run tests/unit/kanbanRevert.test.js`
Expected: PASS (7 tests).

- [ ] **Step 5: Add to coverage include list**

In `frontend/vitest.config.js`, inside `coverage.include`, append:

```js
        'src/utils/kanbanRevert.js',
        'src/utils/kanbanTransitions.js',
```

(The second file arrives in Task 3; adding both now avoids touching this file twice.)

- [ ] **Step 6: Full suite + commit**

Run: `yarn test:run` — expected all green.

```bash
git add frontend/src/utils/kanbanRevert.js frontend/tests/unit/kanbanRevert.test.js frontend/vitest.config.js
git commit -m "feat: add identity-based kanban card revert util"
```

(Append the two Claude trailers from Global Constraints to this and every commit.)

---

### Task 2: Fix `dialogs.jsx` (leak, keyless render, dismiss callback)

**Files:**
- Modify: `frontend/src/utils/dialogs.jsx` (whole file shown below)

**Interfaces:**
- Consumes: nothing new.
- Produces: `createDialog(dialogOptions)` now **returns** the reactive dialog object; options gain optional `onDismiss()` called exactly once whenever the dialog closes for any reason (action `close()`, Esc, X, outside-click). Existing callers (`$dialog` global, form scripts, `ViewControls.vue:1168`) pass no `onDismiss` and are unaffected. Task 3 relies on `createDialog` + `onDismiss`.

No practical unit test here: the module top-level imports `frappe-ui` SFCs and uses JSX, neither of which the vitest config compiles (no vue/vueJsx plugin — deliberately not added; the existing suite doesn't need it and this file's behavior is covered indirectly by Task 3's mocked tests and Task 6's browser verification).

- [ ] **Step 1: Rewrite `frontend/src/utils/dialogs.jsx`**

Replace the entire file with:

```jsx
import { Dialog, ErrorMessage } from 'frappe-ui'
import { reactive, ref } from 'vue'

// Delay before unmounting a closed dialog so the leave transition can finish.
const DIALOG_REMOVE_DELAY_MS = 300

let dialogs = ref([])
let dialogKeyCounter = 0

export function isDialogOpen() {
  return dialogs.value.some((d) => d.show)
}

function onDialogClose(dialog) {
  dialog.show = false
  try {
    dialog.onDismiss?.()
  } finally {
    dialog.onDismiss = null
    setTimeout(() => {
      const index = dialogs.value.indexOf(dialog)
      if (index !== -1) {
        dialogs.value.splice(index, 1)
      }
    }, DIALOG_REMOVE_DELAY_MS)
  }
}

export let Dialogs = {
  name: 'Dialogs',
  render() {
    return dialogs.value.map((dialog) => (
      <Dialog
        key={dialog.key}
        title={dialog.title}
        size={dialog.size}
        icon={dialog.icon}
        position={dialog.position}
        actions={dialog.actions}
        open={dialog.show}
        onUpdate:open={(val) => {
          if (!val) {
            onDialogClose(dialog)
          } else {
            dialog.show = val
          }
        }}
      >
        {{
          default: () => {
            return [
              dialog.message && (
                <p class="text-p-base text-ink-gray-7">{dialog.message}</p>
              ),
              dialog.html && <div v-html={dialog.html} />,
              <ErrorMessage class="mt-2" message={dialog.error} />,
            ]
          },
        }}
      </Dialog>
    ))
  },
}

export function createDialog(dialogOptions) {
  let dialog = reactive(dialogOptions)
  dialog.key = 'dialog-' + dialogKeyCounter++
  dialog.show = false
  setTimeout(() => {
    dialog.show = true
  }, 0)
  dialogs.value.push(dialog)
  return dialog
}
```

Notes for the implementer:
- The monotonic `dialogKeyCounter` replaces `dialogs.value.length` — lengths repeat once entries are removed, and duplicate keys break Vue's keyed patching.
- `onDismiss` is nulled after firing so the callback can't run twice even if `update:open(false)` fires again during the removal window.
- Behavior change vs before: closed dialogs are now removed from `dialogs.value` (previously they accumulated forever — one mounted `<Dialog>` per call for the session's lifetime).

- [ ] **Step 2: Verify existing suite + app still work**

Run (in `frontend/`): `yarn test:run` — expected: all green (nothing imports this module in tests).
Quick smoke: `yarn dev` must still boot without compile errors on `/crm` (existing `$dialog` consumers are load-bearing; actual click-through happens in Task 6).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/dialogs.jsx
git commit -m "fix: remove closed dialogs, key the dialog list, add onDismiss callback"
```

---

### Task 3: `kanbanTransitions` util (confirm + extension seam)

**Files:**
- Create: `frontend/src/utils/kanbanTransitions.js`
- Test: `frontend/tests/unit/kanbanTransitions.test.js`

**Interfaces:**
- Consumes: `createDialog` from `@/utils/dialogs` (Task 2 shape: returns dialog, supports `onDismiss`).
- Produces: `requestKanbanTransition(ctx) → Promise<boolean>` where `ctx = { doctype, itemName, fieldname, fieldLabel, from, to }`. Task 5 imports this exact name from `@/utils/kanbanTransitions`. `confirmKanbanTransition` is exported for tests but callers use `requestKanbanTransition`.

- [ ] **Step 1: Write the failing test**

`frontend/tests/unit/kanbanTransitions.test.js`:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/utils/dialogs', () => ({
  createDialog: vi.fn(),
}))

import { createDialog } from '@/utils/dialogs'
import {
  requestKanbanTransition,
  confirmKanbanTransition,
} from '@/utils/kanbanTransitions'

const ctx = {
  doctype: 'CRM Lead',
  itemName: 'CRM-LEAD-01',
  fieldname: 'status',
  fieldLabel: 'Status',
  from: 'Open',
  to: 'Contacted',
}

function lastDialogOptions() {
  return createDialog.mock.calls.at(-1)[0]
}

describe('confirmKanbanTransition', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('opens a dialog with Cancel and OK actions and the transition message', () => {
    confirmKanbanTransition(ctx)
    expect(createDialog).toHaveBeenCalledOnce()
    const options = lastDialogOptions()
    expect(options.message).toContain('Status')
    expect(options.message).toContain('Open')
    expect(options.message).toContain('Contacted')
    expect(options.actions.map((a) => a.label)).toEqual(['Cancel', 'OK'])
  })

  it('resolves true when OK is clicked', async () => {
    const promise = confirmKanbanTransition(ctx)
    const close = vi.fn()
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close })
    await expect(promise).resolves.toBe(true)
    expect(close).toHaveBeenCalledOnce()
  })

  it('resolves false when Cancel is clicked', async () => {
    const promise = confirmKanbanTransition(ctx)
    const close = vi.fn()
    lastDialogOptions()
      .actions.find((a) => a.label === 'Cancel')
      .onClick({ close })
    await expect(promise).resolves.toBe(false)
    expect(close).toHaveBeenCalledOnce()
  })

  it('resolves false on dismiss (Esc / X / outside click)', async () => {
    const promise = confirmKanbanTransition(ctx)
    lastDialogOptions().onDismiss()
    await expect(promise).resolves.toBe(false)
  })

  it('resolves exactly once when OK is followed by dismiss', async () => {
    const promise = confirmKanbanTransition(ctx)
    const options = lastDialogOptions()
    options.actions.find((a) => a.label === 'OK').onClick({ close: vi.fn() })
    options.onDismiss() // close() always triggers update:open(false) → onDismiss
    await expect(promise).resolves.toBe(true)
  })
})

describe('requestKanbanTransition', () => {
  beforeEach(() => {
    createDialog.mockReset()
  })

  it('delegates to the confirm dialog', async () => {
    const promise = requestKanbanTransition(ctx)
    lastDialogOptions()
      .actions.find((a) => a.label === 'OK')
      .onClick({ close: vi.fn() })
    await expect(promise).resolves.toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `yarn vitest run tests/unit/kanbanTransitions.test.js`
Expected: FAIL — cannot resolve `@/utils/kanbanTransitions`.

- [ ] **Step 3: Write minimal implementation**

`frontend/src/utils/kanbanTransitions.js`:

```js
import { createDialog } from '@/utils/dialogs'

/**
 * Extension seam for kanban transition rules.
 *
 * Future rules (per-doctype allow/block, validations, form-script hooks) go
 * INSIDE this function and must run BEFORE the confirm dialog — never
 * interrupt the user and then veto afterwards. Deliberately not a global
 * check registry: module-level registration duplicates under Vite HMR and
 * leaks state across vitest files.
 *
 * NOTE: client-side only, pure UX. Enforced transition rules belong in
 * server-side validate() (e.g. crm_deal.py), not here.
 *
 * @param {Object} ctx - { doctype, itemName, fieldname, fieldLabel, from, to }
 * @returns {Promise<boolean>} whether the transition may proceed
 */
export async function requestKanbanTransition(ctx) {
  return confirmKanbanTransition(ctx)
}

/**
 * Cancel/OK confirmation for a kanban column transition.
 * Resolves exactly once; Esc, X and outside-click count as Cancel.
 *
 * @param {Object} ctx - { fieldLabel, from, to } (rest of ctx unused here)
 * @returns {Promise<boolean>}
 */
export function confirmKanbanTransition({ fieldLabel, from, to }) {
  return new Promise((resolve) => {
    let resolved = false
    const resolveOnce = (value) => {
      if (resolved) return
      resolved = true
      resolve(value)
    }

    createDialog({
      title: __('Confirm change'),
      message: __('Change {0} from "{1}" to "{2}"?', [fieldLabel, from, to]),
      onDismiss: () => resolveOnce(false),
      actions: [
        {
          label: __('Cancel'),
          onClick: ({ close }) => {
            resolveOnce(false)
            close()
          },
        },
        {
          label: __('OK'),
          variant: 'solid',
          onClick: ({ close }) => {
            resolveOnce(true)
            close()
          },
        },
      ],
    })
  })
}
```

Implementer notes:
- frappe-ui `Dialog` actions do **not** auto-close; `onClick` receives a context whose `.close` must be called (see `frappe-ui/src/components/Dialog/Dialog.vue` `reactiveActions`). Destructuring `({ close })` is the modern API.
- `close()` flows through `update:open(false)` → `onDismiss` → `resolveOnce(false)`, which the `resolved` flag makes a no-op after OK. That's the "resolves exactly once" test.
- `__` is a global in app and test contexts; no import.

- [ ] **Step 4: Run test to verify it passes**

Run: `yarn vitest run tests/unit/kanbanTransitions.test.js`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite + commit**

Run: `yarn test:run` — all green.

```bash
git add frontend/src/utils/kanbanTransitions.js frontend/tests/unit/kanbanTransitions.test.js
git commit -m "feat: add kanban transition confirm with extension seam"
```

---

### Task 4: `KanbanView.vue` — extract cross-column branch, enrich payload

**Files:**
- Modify: `frontend/src/components/Kanban/KanbanView.vue:256-277` (the `updateColumn` function)

**Interfaces:**
- Consumes: nothing new.
- Produces: the `update` event's cross-column payload becomes `{ item, to, from, oldIndex, kanban_columns }` (adds `from`, `oldIndex`). Task 5's `updateKanbanSettings` reads `data.from` and `data.oldIndex`. Same-column payload `{ kanban_columns, fetchNewColumns }` is unchanged, as are all six `updateColumn` call sites (template lines 9, 47, 80, 171 and functions `actions`/`addColumn`).

- [ ] **Step 1: Replace `updateColumn` (lines 256-277)**

Replace:

```js
function updateColumn(d, fetchNewColumns = false) {
  let toColumn = d?.to?.dataset.column
  let fromColumn = d?.from?.dataset.column
  let itemName = d?.item?.dataset.name

  let _columns = []
  columns.value.forEach((col) => {
    col.column['order'] = col.data.map((d) => d.name)
    if (col.column.page_length) {
      delete col.column.page_length
    }
    _columns.push(col.column)
  })

  let data = { kanban_columns: _columns, fetchNewColumns }

  if (toColumn != fromColumn) {
    data = { item: itemName, to: toColumn, kanban_columns: _columns }
  }

  emit('update', data)
}
```

with:

```js
function updateColumn(d, fetchNewColumns = false) {
  let toColumn = d?.to?.dataset.column
  let fromColumn = d?.from?.dataset.column

  if (toColumn != fromColumn) {
    moveCardBetweenColumns(d)
    return
  }

  emit('update', { kanban_columns: serializeColumns(), fetchNewColumns })
}

// Cross-column drag: vuedraggable has already moved the card optimistically.
// ViewControls owns confirm/save/revert; oldIndex is only a position hint for
// the revert (the card itself is re-resolved by name there).
function moveCardBetweenColumns(d) {
  emit('update', {
    item: d.item.dataset.name,
    to: d.to.dataset.column,
    from: d.from.dataset.column,
    oldIndex: d.oldIndex,
    kanban_columns: serializeColumns(),
  })
}

function serializeColumns() {
  let _columns = []
  columns.value.forEach((col) => {
    col.column['order'] = col.data.map((d) => d.name)
    if (col.column.page_length) {
      delete col.column.page_length
    }
    _columns.push(col.column)
  })
  return _columns
}
```

Behavior notes (why this is safe for every existing caller):
- Callers passing no event / a MouseEvent (`updateColumn()`, `@click="updateColumn"`, `updateColumn(null, true)`): `toColumn` and `fromColumn` are both `undefined`, `undefined != undefined` is false → same-column branch, exactly as before.
- Only a real cross-column SortableJS event (with `to`/`from`/`item` elements) reaches `moveCardBetweenColumns`, so the non-optional-chained property access there is intentional.

- [ ] **Step 2: Verify**

Run: `yarn test:run` — all green (no component tests exist; this guards against accidental util imports).
`yarn dev` boots; on `/crm` kanban a same-column reorder and the Reload Columns button still work (full drag flow is still fire-and-forget until Task 5 — cross-column now also carries `from`/`oldIndex`, which today's ViewControls ignores).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Kanban/KanbanView.vue
git commit -m "feat: emit from/oldIndex on cross-column kanban drags"
```

---

### Task 5: `ViewControls.vue` — confirm, save, revert

**Files:**
- Modify: `frontend/src/components/ViewControls.vue` (imports at ~line 303-351; `updateKanbanSettings` at lines 976-1022)

**Interfaces:**
- Consumes: `requestKanbanTransition` (Task 3), `revertCardMove` (Task 1), payload with `from`/`oldIndex` (Task 4). Already in scope in this file: `call`, `toast` (frappe-ui import line ~329-337), `getFields` (line 740: `const { getFields } = getMeta(props.doctype)`), `list` (defineModel, line 372), `view`, `defaultParams`, `getParams`, `route`, `createOrUpdateStandardView`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add imports**

After the `import { getMeta } from '@/stores/meta'` line (~327), add:

```js
import { requestKanbanTransition } from '@/utils/kanbanTransitions'
import { revertCardMove } from '@/utils/kanbanRevert'
```

- [ ] **Step 2: Replace the item-branch of `updateKanbanSettings`**

Replace (lines 976-985):

```js
function updateKanbanSettings(data) {
  if (data.item && data.to) {
    call('frappe.client.set_value', {
      doctype: props.doctype,
      name: data.item,
      fieldname: view.value.column_field,
      value: data.to,
    })
    return
  }
```

with:

```js
function updateKanbanSettings(data) {
  if (data.item && data.to) {
    handleKanbanTransition(data)
    return
  }
```

and add below `updateKanbanSettings` (after line 1022):

```js
async function handleKanbanTransition(data) {
  const fieldname = view.value.column_field

  const revert = () => {
    const reverted = revertCardMove({
      columns: list.value?.data?.data || [],
      itemName: data.item,
      from: data.from,
      to: data.to,
      oldIndex: data.oldIndex,
    })
    // Board changed underneath (Load More / view switch) — resync instead
    if (!reverted) list.value.reload()
  }

  const fieldLabel =
    getFields()?.find((f) => f.fieldname === fieldname)?.label || fieldname

  const confirmed = await requestKanbanTransition({
    doctype: props.doctype,
    itemName: data.item,
    fieldname,
    fieldLabel,
    from: data.from,
    to: data.to,
  })
  if (!confirmed) {
    revert()
    return
  }

  try {
    await call('frappe.client.set_value', {
      doctype: props.doctype,
      name: data.item,
      fieldname,
      value: data.to,
    })
  } catch (error) {
    revert()
    toast.error(
      error.messages?.[0] || error.message || __('Failed to update {0}', [fieldLabel]),
    )
    return
  }

  // Keep the moved card's own field in sync so its badge doesn't go stale
  const targetColumn = (list.value?.data?.data || []).find(
    (col) => col.column?.name == data.to,
  )
  const card = targetColumn?.data?.find((row) => row.name == data.item)
  if (card) card[fieldname] = data.to

  // Persist card ordering (was silently discarded on cross-column moves)
  if (data.kanban_columns?.length) {
    if (!defaultParams.value) {
      defaultParams.value = getParams()
    }
    list.value.params = defaultParams.value
    list.value.params.kanban_columns = data.kanban_columns
    view.value.kanban_columns = data.kanban_columns
    if (!route.query.view) {
      createOrUpdateStandardView()
    }
  }
}
```

Implementer notes:
- `updateKanbanSettings` itself stays synchronous — pages call it fire-and-forget (`@update="(data) => viewControls.updateKanbanSettings(data)"`), nothing awaits it.
- The ordering-persist block deliberately mirrors the existing else-branch (lines 994-1001, 1019-1021) **minus** `list.value.reload()` — the board is already visually correct after OK; a reload would flash. Custom-view persistence (`persistCustomView`) is intentionally NOT added here because the existing kanban-settings branch doesn't call it either — same behavior envelope.
- `error.messages` is the frappe server-message array on rejected `call`s (e.g. the `crm_deal.py` lost_reason ValidationError); `error.message` covers network errors.

- [ ] **Step 3: Verify**

Run: `yarn test:run` — all green.
Manual (needs `bench start` + `yarn dev` running, logged-in session on `http://localhost:8080/crm`):
1. Leads kanban → drag a card to another column → modal appears with "Change Status from … to …".
2. Cancel → card returns to original column+position; network tab shows NO `set_value`.
3. Drag again → OK → `set_value` fires; card stays; badge color/label updates without reload.
4. Reload the page → the card is still in the new column at the dropped position (ordering persisted).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ViewControls.vue
git commit -m "feat: confirm kanban transitions, revert on cancel or failed save"
```

---

### Task 6: Full verification, lint, docs

**Files:**
- Modify (only if lint/prettier demand): files touched in Tasks 1-5
- Modify: `specs/kanban-transition-confirm.md` (status line only)

- [ ] **Step 1: Full automated pass**

In `frontend/`:

```bash
yarn test:run
npx prettier --check src/utils/kanbanRevert.js src/utils/kanbanTransitions.js src/utils/dialogs.jsx src/components/Kanban/KanbanView.vue src/components/ViewControls.vue tests/unit/kanbanRevert.test.js tests/unit/kanbanTransitions.test.js
npx eslint src/utils/kanbanRevert.js src/utils/kanbanTransitions.js src/utils/dialogs.jsx src/components/Kanban/KanbanView.vue src/components/ViewControls.vue
```

Expected: tests green; fix any prettier/eslint complaints (`prettier --write` the same list) and re-run.

- [ ] **Step 2: Browser verification checklist** (all three kanban consumers)

With backend + frontend dev servers running and a logged-in session:

1. **Deals** kanban: drag a deal to a "Lost"-type status column → OK → server rejects (missing `lost_reason`) → card reverts + error toast with the server message. This is the headline bug fix — verify it explicitly.
2. **Leads** kanban: Cancel via button, Esc, X, and outside-click — all four revert, no save, and the flow works again on the next drag (single-resolution).
3. **Tasks** kanban: OK path persists; badge updates; page reload keeps column + position.
4. Load More on the source column while the modal is open, then Cancel → board reloads cleanly (revert falls back).
5. Same-column reorder and column reorder (drag column header) → no modal.
6. Open a few `$dialog`-based dialogs elsewhere (e.g. delete-view confirmation in view controls) → still behave, close properly (dialogs.jsx regression check).
7. Kanban grouped by a non-status Link/Select field (Kanban Settings → change column field) → modal appears with that field's label.

- [ ] **Step 3: Mark spec implemented + commit**

In `specs/kanban-transition-confirm.md` change the `Status:` line to `Status: implemented (2026-07-27)`.

```bash
git add specs/kanban-transition-confirm.md
git commit -m "docs: mark kanban transition confirm spec implemented"
```

---

## Self-review notes (already applied)

- Spec coverage: pipeline seam (T3), confirm-last ordering documented in-module (T3), identity revert + reload fallback (T1/T5), badge fix (T5), ordering persist (T5), dialogs.jsx leak/key/dismiss (T2), KanbanView extraction with sync `updateColumn` (T4), error toast after revert (T5), tests + coverage include (T1/T3), manual matrix incl. Lost-deal server rejection (T6). Out-of-scope items from the spec have no tasks — correct.
- Deliberate deviation from spec: none functional. `confirmKanbanTransition` lives in `kanbanTransitions.js` rather than a separate file; `revertCardMove` sits in `kanbanRevert.js` exactly as specced.
- Type consistency: payload keys `item/to/from/oldIndex/kanban_columns` and ctx keys `doctype/itemName/fieldname/fieldLabel/from/to` are identical across Tasks 3, 4, 5.
