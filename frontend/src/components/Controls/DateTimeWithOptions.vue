<!--
  CRM-owned Datetime control with a per-field business-hour time dropdown (TXB-238).

  The shared frappe-ui DateTimePicker embeds a TimePicker whose option list
  always starts at 00:00 and exposes no field-level start, so a coaching field
  cannot say "offer times from 07:00". This control composes the same supported
  pickers — frappe-ui DatePicker for the date, a typeable TextInput + Popover
  menu for the time — and feeds the time menu a generated `optionsStart`-23:45
  list (see `@/utils/timePicker`). The menu is a suggestion only: a coach can
  still type an earlier exception (e.g. 06:30) and it is saved unchanged.

  The model contract is identical to the DateTimePicker it stands in for: it
  reads and emits the canonical `"YYYY-MM-DD HH:mm:ss"` string via `value` /
  `@change`, so `Field.vue`'s `fieldChange` path is untouched. `Field.vue`
  selects this control only for Datetime fields carrying `time_options_start`;
  every other Datetime field keeps the shared DateTimePicker.
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
    <Popover class="flex-1" placement="bottom-start">
      <template #target="{ open, togglePopover }">
        <TextInput
          class="w-full"
          type="text"
          :modelValue="parts.time"
          :placeholder="__('HH:mm')"
          input-class="border-none"
          @focus="!open && togglePopover()"
          @change="onTimeInput($event.target.value)"
          @keydown.enter="onTimeInput($event.target.value)"
        />
      </template>
      <template #body="{ close }">
        <div
          class="my-1 rounded-lg bg-surface-elevation-2 p-1 shadow-2xl ring-1 ring-black ring-opacity-5 focus:outline-none"
        >
          <div role="listbox" class="flex flex-col">
            <button
              v-for="option in timeOptions"
              :key="option"
              type="button"
              class="cursor-pointer rounded px-2 py-1 text-left text-sm text-ink-gray-8 hover:bg-surface-gray-2"
              @click="selectTime(option, close)"
            >
              {{ option }}
            </button>
          </div>
        </div>
      </template>
    </Popover>
  </div>
</template>

<script setup>
import {
  timePickerAttrs,
  generateTimeOptions,
  splitDatetime,
  combineDatetime,
} from '@/utils/timePicker'
import { DatePicker, Popover, TextInput } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  // Canonical "YYYY-MM-DD HH:mm:ss" datetime, matching the DateTimePicker contract.
  value: { type: String, default: '' },
  // Date display format from getFormat(); the time half uses the generated menu.
  dateFormat: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  // Daily start of the generated dropdown, e.g. "07:00" (field.time_options_start).
  optionsStart: { type: String, default: '07:00' },
})

const emit = defineEmits(['change'])

const parts = computed(() => splitDatetime(props.value))

const timeOptions = computed(() => generateTimeOptions(props.optionsStart))

// frappe-ui's DatePicker usually emits the plain "YYYY-MM-DD" string, but mirror the
// unwrap Field.vue's fieldChange applies so an { value } payload can never stringify into
// the datetime as "[object Object]".
function unwrap(v) {
  return typeof v === 'object' && v !== null && 'value' in v ? v.value : v
}

function onDateChange(date) {
  emit('change', combineDatetime(unwrap(date), parts.value.time))
}

function onTimeInput(time) {
  emit('change', combineDatetime(parts.value.date, time))
}

function selectTime(time, close) {
  emit('change', combineDatetime(parts.value.date, time))
  close?.()
}
</script>
