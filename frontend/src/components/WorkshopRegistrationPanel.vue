<template>
  <div class="flex flex-col gap-2 border-b px-4 py-3 sm:px-6">
    <div class="flex items-center justify-between">
      <div class="text-sm text-ink-gray-5">{{ __('Registration link') }}</div>
      <Button
        v-if="!link"
        size="sm"
        :label="__('Generate link')"
        :loading="generating"
        @click="generate"
      />
    </div>
    <template v-if="link">
      <div class="flex items-center gap-1">
        <input
          class="form-input w-full truncate text-sm"
          readonly
          :value="link"
          @focus="$event.target.select()"
        />
        <Button
          size="sm"
          variant="ghost"
          icon="lucide-copy"
          :tooltip="__('Copy link')"
          @click="copyToClipboard(link)"
        />
        <Button
          size="sm"
          variant="ghost"
          icon="lucide-external-link"
          :tooltip="__('Open registration page')"
          @click="openLink"
        />
      </div>
      <div v-if="svg" class="flex flex-col items-center gap-2 pt-1">
        <!-- server-generated SVG (pyqrcode), not user content -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="w-36 rounded border bg-white p-1" v-html="svg" />
        <div class="flex gap-2">
          <a :href="downloadUrl('svg')">
            <Button size="sm" :label="__('Download SVG')" />
          </a>
          <a :href="downloadUrl('png')">
            <Button size="sm" :label="__('Download PNG')" />
          </a>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { copyToClipboard } from '@/utils'
import { resolveRegistrationLinkHydration } from '@/utils/registrationLink'
import { call, toast } from 'frappe-ui'
import { ref, watch } from 'vue'

const props = defineProps({
  deal: { type: Object, required: true },
})

const link = ref(props.deal.custom_registration_link || '')
const svg = ref('')
const generating = ref(false)

const QR_METHOD = 'crm.txb.api.registration.registration_qr'

async function loadQr() {
  if (!link.value) return
  try {
    const res = await call(QR_METHOD, { deal: props.deal.name })
    svg.value = res?.svg || ''
  } catch (e) {
    console.error('[WorkshopRegistrationPanel] Failed to load QR', e)
  }
}

async function generate() {
  generating.value = true
  try {
    const res = await call(
      'crm.txb.api.registration.generate_registration_link',
      {
        deal: props.deal.name,
      },
    )
    link.value = res.link
    toast.success(__('Registration link generated'))
    await loadQr()
  } catch (e) {
    toast.error(
      e?.messages?.[0] || __('Could not generate the registration link'),
    )
  } finally {
    generating.value = false
  }
}

function openLink() {
  window.open(link.value, '_blank', 'noopener,noreferrer')
}

function downloadUrl(fmt) {
  const q = new URLSearchParams({ deal: props.deal.name, fmt, download: 1 })
  return `/api/method/${QR_METHOD}?${q}`
}

watch(
  () => props.deal.custom_registration_link,
  (v) => {
    // Any non-empty persisted link must hydrate the QR at least once — including
    // the initial render where it already equals the local link — so the QR
    // survives a deal refresh. Empty links never request a QR.
    const { link: next, loadQr: shouldLoad } = resolveRegistrationLinkHydration(v)
    if (!shouldLoad) return
    link.value = next
    loadQr()
  },
  { immediate: true },
)
</script>
