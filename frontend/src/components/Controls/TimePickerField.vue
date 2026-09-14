<!--
  CRM-owned Safari-iOS-compatible trigger wrapper for frappe-ui's TimePicker (TXB-245).

  frappe-ui's packaged TimePicker opens its option list from two triggers: the
  text input (via `click`/`focus`) and a chevron suffix whose only handler is
  `@mousedown.prevent="togglePopover"`. On Safari iOS a `mousedown` on a bare
  `<span>` is synthesized late and unreliably, and the `.prevent` cancels the
  focus that would otherwise open the popover — so tapping the chevron does
  nothing and the standalone Meeting time field cannot be opened on iOS.

  Rather than patch the installed dependency, this wrapper keeps the same
  TimePicker but supplies a pointer/touch-compatible trigger:

  - `open-on-focus` is enabled so a tap that focuses the input opens the list on
    iOS (the packaged default only opens on `click`, which iOS delivers late for
    the field).
  - The chevron `#suffix` is overridden to add `@touchend.prevent`, which fires
    on the initial iOS tap and suppresses the unreliable synthesized mouse
    sequence. `@mousedown.prevent` is retained so desktop toggle-to-close (which
    depends on the input not blurring first) behaves exactly as before.

  Everything else is a pass-through: the wrapper reads/emits the same
  `modelValue`/`update:modelValue` contract (TXB-236), forwards the generated
  07:00–23:45 option list (TXB-241), and stays fully typeable, so manual times
  still commit and persist. It also carries the shared popover class so the CRM
  stacking/scroll CSS keeps applying (see `@/utils/timePicker`).
-->
<template>
  <TimePicker
    v-bind="timePickerAttrs()"
    :model-value="modelValue"
    :options="options"
    :format="format"
    :placeholder="placeholder"
    :open-on-focus="true"
    input-class="border-none"
    @update:model-value="(v) => emit('update:modelValue', v)"
  >
    <template #suffix="{ togglePopover }">
      <span
        class="lucide-chevron-down size-4 cursor-pointer"
        aria-hidden="true"
        @mousedown.prevent="togglePopover"
        @touchend.prevent="togglePopover"
      />
    </template>
  </TimePicker>
</template>

<script setup>
import { timePickerAttrs } from '@/utils/timePicker'
import { TimePicker } from 'frappe-ui'

defineProps({
  // Canonical "HH:mm[:ss]" time, matching the TimePicker modelValue contract.
  modelValue: { type: String, default: '' },
  // `{ value, label }[]` dropdown options (07:00–23:45 for Discovery), or null to
  // keep the picker's default list. The control stays typeable either way.
  options: { type: Array, default: null },
  format: { type: String, default: '' },
  placeholder: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])
</script>
