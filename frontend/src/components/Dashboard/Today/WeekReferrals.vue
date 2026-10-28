<template>
  <WidgetCard
    :title="__('Referrals this week')"
    :icon="LucideUsers"
    :loading="thisWeek.loading && !thisWeek.data"
    @expand="openDialog"
  >
    <template #header-extra>
      <span class="text-sm text-ink-gray-5">{{ weekLabel(thisWeek.data) }}</span>
    </template>
    <div class="flex flex-wrap items-center gap-x-6 gap-y-2 px-3.5 py-3">
      <div class="flex items-baseline gap-2">
        <span class="text-3xl font-semibold text-ink-gray-9">
          {{ thisWeek.data?.total ?? 0 }}
        </span>
        <span class="text-base text-ink-gray-6">{{ __('referred leads') }}</span>
      </div>
      <div
        v-for="rep in band.shown"
        :key="rep.user"
        class="flex items-center gap-2 rounded-full border border-outline-gray-2 py-1 pl-1 pr-2.5"
        :class="{ 'bg-surface-gray-2': rep.user == me }"
      >
        <UserAvatar :user="rep.user" size="sm" />
        <span class="text-base text-ink-gray-8" :class="{ 'font-semibold': rep.user == me }">
          {{ rep.user == me ? __('You') : getUser(rep.user).full_name }}
        </span>
        <span class="text-base font-semibold text-ink-gray-9">+{{ rep.points }}</span>
      </div>
      <Button v-if="band.more" variant="subtle" @click="openDialog">
        {{ __('+{0} more', [band.more]) }}
      </Button>
    </div>
  </WidgetCard>

  <Dialog v-model:open="show" size="4xl">
    <template #body>
      <div class="px-5 pb-5 pt-4">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <h3 class="text-xl font-semibold text-ink-gray-9">
            {{ __('Referrals this week') }}
          </h3>
          <div class="ml-auto flex items-center gap-1">
            <Button
              icon="lucide-chevron-left"
              :aria-label="__('Previous week')"
              @click="shiftWeek(-7)"
            />
            <span class="px-2 text-base text-ink-gray-8">{{ weekLabel(week.data) }}</span>
            <Button
              icon="lucide-chevron-right"
              :aria-label="__('Next week')"
              :disabled="weekOf >= todayStr"
              @click="shiftWeek(7)"
            />
          </div>
          <Button
            variant="ghost"
            icon="lucide-x"
            :aria-label="__('Close')"
            @click="show = false"
          />
        </div>
        <div class="flex max-h-[60vh] flex-wrap gap-5 overflow-auto">
          <section class="w-64 shrink-0" :aria-label="__('Leaderboard')">
            <div class="mb-2 flex items-baseline gap-2">
              <span class="text-3xl font-semibold text-ink-gray-9">
                {{ week.data?.total ?? 0 }}
              </span>
              <span class="text-base text-ink-gray-6">{{ __('referred leads') }}</span>
            </div>
            <ol>
              <li
                v-for="(rep, i) in week.data?.reps || []"
                :key="rep.user"
                class="flex items-center gap-2.5 rounded px-2 py-1.5"
                :class="{ 'bg-surface-gray-2': rep.user == me }"
              >
                <span class="w-4 text-right text-sm text-ink-gray-5">{{ i + 1 }}</span>
                <UserAvatar :user="rep.user" size="sm" />
                <span class="flex-1 truncate text-base text-ink-gray-8">
                  {{ rep.user == me ? __('You') : getUser(rep.user).full_name }}
                </span>
                <span class="text-base font-semibold text-ink-gray-9">+{{ rep.points }}</span>
              </li>
            </ol>
            <p v-if="!week.data?.reps?.length" class="text-base text-ink-gray-5">
              {{ __('No referrals this week.') }}
            </p>
          </section>
          <section class="min-w-0 flex-1" :aria-label="__('Referred leads')">
            <table class="w-full text-base">
              <thead>
                <tr class="text-left text-ink-gray-6">
                  <th class="py-2 pr-3 font-medium">{{ __('Referred lead') }}</th>
                  <th class="py-2 pr-3 font-medium">{{ __('Referred by') }}</th>
                  <th class="py-2 pr-3 font-medium">{{ __('Lead owner') }}</th>
                  <th class="py-2 font-medium">{{ __('Created') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="lead in week.data?.leads || []"
                  :key="lead.name"
                  class="border-t border-outline-gray-1"
                >
                  <td class="py-2 pr-3">
                    <router-link
                      :to="{ name: 'Lead', params: { leadId: lead.name } }"
                      class="text-ink-gray-9 hover:underline"
                      @click="show = false"
                    >
                      {{ lead.lead_name || lead.name }}
                    </router-link>
                  </td>
                  <td class="py-2 pr-3">
                    <router-link
                      v-if="referrer(lead).route"
                      :to="referrer(lead).route"
                      class="text-ink-gray-7 hover:underline"
                      @click="show = false"
                    >
                      {{ referrer(lead).label }} · {{ lead.reference_title }}
                    </router-link>
                  </td>
                  <td class="py-2 pr-3 text-ink-gray-7">
                    {{ lead.lead_owner && getUser(lead.lead_owner).full_name }}
                  </td>
                  <td class="whitespace-nowrap py-2 text-ink-gray-5">
                    {{ formatDate(lead.creation, 'ddd HH:mm') }}
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="week.data && week.data.leads.length < week.data.total"
              class="pt-3 text-sm text-ink-gray-5"
            >
              {{ __('Leads you cannot open are counted but not listed.') }}
            </p>
          </section>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import WidgetCard from './WidgetCard.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import LucideUsers from '~icons/lucide/users'
import { taskReference } from '@/components/ContactInactivity/taskReference'
import { usersStore } from '@/stores/users'
import { bandReps } from '@/utils/todayWidgets'
import { formatDate } from '@/utils'
import { createResource, Dialog, dayjs } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  user: { type: String, default: null },
})

const { getUser } = usersStore()
const me = getUser().name
const show = ref(false)
const todayStr = formatDate(new Date(), 'YYYY-MM-DD')
const weekOf = ref(todayStr)

const thisWeek = createResource({
  url: 'crm.api.dashboard.get_week_referrals',
  makeParams: () => ({ user: props.user }),
  auto: true,
})
const week = createResource({
  url: 'crm.api.dashboard.get_week_referrals',
  makeParams: () => ({ user: props.user, week_of: weekOf.value }),
})
watch(
  () => props.user,
  () => {
    thisWeek.reload()
    if (show.value) week.reload()
  },
)

const band = computed(() => bandReps(thisWeek.data?.reps, me))

function weekLabel(data) {
  if (!data) return ''
  return `${formatDate(data.week_start, 'MMM D')} – ${formatDate(data.week_end, 'MMM D')}`
}

function openDialog() {
  weekOf.value = todayStr
  week.reload()
  show.value = true
}

function shiftWeek(days) {
  weekOf.value = dayjs(weekOf.value).add(days, 'day').format('YYYY-MM-DD')
  week.reload()
}

function referrer(lead) {
  return taskReference(lead.referred_by_type, lead.referred_by)
}
</script>
