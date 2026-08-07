<template>
  <Popover placement="bottom-end">
    <template #target="{ togglePopover }">
      <div class="flex items-center" @click="togglePopover">
        <component
          :is="assignees?.length == 1 ? 'Button' : 'div'"
          v-if="assignees?.length"
        >
          <MultipleAvatar :avatars="assignees" />
        </component>
        <Button v-else :label="__('Assign To')" />
      </div>
    </template>
    <template #body="{ isOpen }">
      <AssignToBody
        v-show="isOpen"
        v-model="assignees"
        :docname="docname"
        :doctype="doctype"
        :open="isOpen"
        :onUpdate="saveAssignees"
      />
    </template>
  </Popover>
</template>
<script setup>
import MultipleAvatar from '@/components/MultipleAvatar.vue'
import AssignToBody from '@/components/AssignToBody.vue'
import { Popover } from 'frappe-ui'

const props = defineProps({
  doctype: { type: String, default: '' },
  docname: { type: String, default: '' },
})

const assignees = defineModel({ type: Array, default: () => [] })

/**
 * Assignment used to write the owner field: adding an assignee to an unowned record made
 * them its owner, and removing the owner from the assignees handed it to "the next
 * available assignee". TXB-106 reserves owner changes for Admins, and both of those were
 * a way around that guard -- the first also being exactly the automatic assignment the
 * ticket forbids. Assignment now only assigns.
 */
async function saveAssignees(
  addedAssignees,
  removedAssignees,
  addAssignees,
  removeAssignees,
) {
  if (removedAssignees.length) await removeAssignees.submit(removedAssignees)
  if (addedAssignees.length) await addAssignees.submit(addedAssignees)
}
</script>
