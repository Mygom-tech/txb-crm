import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import EventIcon from '@/components/Icons/EventIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import {
  activitySource,
  contactTabCounts,
  leadSourceNames,
} from '@/utils/contactActivity'
import { createResource, getCachedResource } from 'frappe-ui'
import { computed, watch } from 'vue'

// The Contact tabs the shared Activities timeline renders; Calls, Notes and Deals keep their
// Contact-specific views.
export const TIMELINE_TABS = ['Activity', 'Emails', 'Comments', 'Events', 'Tasks']

// The Contact page tabs, in the Lead/Deal order with Deals last, with counts from the
// aggregate `feed()` and `deals()`.
export function contactTabs({ deals, feed }) {
  const counts = computed(() => contactTabCounts(feed()))
  const count = (name) => computed(() => counts.value[name])
  return [
    { name: 'Activity', label: __('Activity'), icon: ActivityIcon },
    { name: 'Emails', label: __('Emails'), icon: EmailIcon, count: count('Emails') },
    { name: 'Comments', label: __('Comments'), icon: CommentIcon, count: count('Comments') },
    { name: 'Events', label: __('Events'), icon: EventIcon, count: count('Events') },
    { name: 'Calls', label: __('Calls'), icon: PhoneIcon, count: count('Calls') },
    { name: 'Tasks', label: __('Tasks'), icon: TaskIcon, count: count('Tasks') },
    { name: 'Notes', label: __('Notes'), icon: NoteIcon, count: count('Notes') },
    {
      name: 'Deals',
      label: __('Deals'),
      icon: DealsIcon,
      count: computed(() => deals()?.length),
    },
  ]
}

// The Contact's deduplicated, source-tagged aggregate stream, under the Activity timeline's
// cache key. The page creates it (fetching fresh on every visit); its tabs reuse that copy
// instead of refetching on every tab switch.
export function contactActivitiesResource(docname) {
  return createResource({
    url: 'crm.api.activities.get_activities',
    params: { name: docname },
    cache: ['activity', docname],
    auto: true,
    transform: ([versions, calls, notes, tasks, attachments]) => {
      return { versions, calls, notes, tasks, attachments }
    },
  })
}

// One category (`notes`, `calls`) of that stream, newest first by `sortKey`.
export function useContactActivityFeed(docname, category, sortKey) {
  const activities =
    getCachedResource(['activity', docname]) ||
    contactActivitiesResource(docname)

  const items = computed(() =>
    [...(activities.data?.[category] || [])].sort(
      (a, b) => new Date(b[sortKey]) - new Date(a[sortKey]),
    ),
  )

  // Best-effort email labels for archived-Lead sources, mirroring the activity log: a Lead the
  // user cannot read simply keeps its docname label, so a partial lookup never breaks a table.
  const leadSourceDocnames = computed(() => leadSourceNames(items.value))

  const leadEmailsResource = createResource({
    url: 'frappe.client.get_list',
    cache: ['contactLeadEmails', docname, category],
    makeParams: () => ({
      doctype: 'CRM Lead',
      filters: [['name', 'in', leadSourceDocnames.value]],
      fields: ['name', 'email'],
      limit_page_length: 0,
    }),
  })

  watch(
    leadSourceDocnames,
    (names) => {
      if (names?.length) leadEmailsResource.reload()
    },
    { immediate: true },
  )

  const leadEmails = computed(() => {
    const map = {}
    for (const row of leadEmailsResource.data || []) {
      if (row.email) map[row.name] = row.email
    }
    return map
  })

  function sourceLabel(activity) {
    return activitySource(activity, leadEmails.value)?.label || ''
  }

  return { activities, items, leadEmails, sourceLabel }
}
