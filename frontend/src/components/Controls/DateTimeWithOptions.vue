<!--
  CRM-owned Datetime control with a per-field business-hour time dropdown (TXB-239).

  TXB-238 shipped a split date-plus-plain-TextInput here: the time half was a
  bare `TextInput` with a hand-rolled option menu, which QA reported as a raw
  manual input rather than the normal picker. This restores the established CRM
  interaction by composing the two supported frappe-ui pickers — `DatePicker`
  for the date and the real `TimePicker` for the time — so the time half is once
  again a proper dropdown with keyboard navigation, typing, and the shared popover
  styling, not a bespoke text box.

  The one thing frappe-ui's combined `DateTimePicker` cannot do is start its time
  list anywhere but 00:00 (it embeds `TimePicker` without forwarding an option
  list), so a coaching field cannot say "offer times from 07:00". This adapter
  feeds the `TimePicker` an explicit `optionsStart`-23:45 list (see
  `@/utils/timePicker`) to constrain *only* the displayed options. The list is a
  suggestion: `TimePicker` stays typeable, so a coach can still type an earlier
  exception (e.g. 06:30) and it is committed and saved unchanged (AC-2).

  The model contract matches the DateTimePicker it stands in for: it reads and
  emits the canonical `"YYYY-MM-DD HH:mm:ss"` string via `value` / `@change`, so
  `Field.vue`'s `fieldChange` path is untouched. `Field.vue` selects this control
  only for Datetime fields carrying `time_options_start`; every other Datetime
  field keeps the shared DateTimePicker.
-->
<template>
  <div v-bind="timePickerAttrs()" class="flex gap-1">
    <DatePicker
      class="flex-1"
      :value="parts.date"
      :format="dateFormat"
      :placeholder="placeholder"
      input-class="border-none"
      @change="onDateChange"
    />
    <TimePicker
      class="flex-1"
      :model-value="parts.time"
      :options="timeOptions"
      :interval="stepMinutes"
      :placeholder="__('HH:mm')"
      input-class="border-none"
      @change="onTimeChange"
    />
  </div>
</template>

<script setup>
import {
  timePickerAttrs,
  generateTimeOptions,
  splitDatetime,
  combineDatetime,
} from '@/utils/timePicker'
import { DatePicker, TimePicker } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  // Canonical "YYYY-MM-DD HH:mm:ss" datetime, matching the DateTimePicker contract.
  value: { type: String, default: '' },
  // Date display format from getFormat(); the time half uses the generated menu.
  dateFormat: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  // Daily start of the generated dropdown, e.g. "07:00" (field.time_options_start).
  optionsStart: { type: String, default: '07:00' },
  // Spacing between generated options; kept at 15 to match every CRM datetime field.
  stepMinutes: { type: Number, default: 15 },
})

const emit = defineEmits(['change'])

const parts = computed(() => splitDatetime(props.value))

// frappe-ui's TimePicker takes `{ value, label }[]`; the generated "HH:mm" list is
// both the value and the label. Passing `options` overrides TimePicker's default
// 00:00-start list without disabling typing, so earlier exceptions still commit.
const timeOptions = computed(() =>
  generateTimeOptions(props.optionsStart, undefined, props.stepMinutes).map(
    (time) => ({ value: time, label: time }),
  ),
)

// frappe-ui's DatePicker usually emits the plain "YYYY-MM-DD" string, but mirror the
// unwrap Field.vue's fieldChange applies so an { value } payload can never stringify into
// the datetime as "[object Object]".
function unwrap(v) {
  return typeof v === 'object' && v !== null && 'value' in v ? v.value : v
}

function onDateChange(date) {
  emit('change', combineDatetime(unwrap(date), parts.value.time))
}

function onTimeChange(time) {
  emit('change', combineDatetime(parts.value.date, unwrap(time)))
}
</script>
