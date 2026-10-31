<script setup lang="ts">
import { ArrowDown, ArrowUp } from '@lucide/vue'
import { computed, ref } from 'vue'

import type { OnuTraffic } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import TrafficChart from '@/components/TrafficChart.vue'
import { timeAgo } from '@/lib/format'
import { oltBrand } from '@/lib/labels'
import { byDay, byHour, formatBps, formatBytes, totals } from '@/lib/traffic'

const props = defineProps<{
  traffic: OnuTraffic | undefined
  loading: boolean
  error: string | null
  driver: string
}>()

type Range = '24h' | '7d' | '30d'
const RANGES: { value: Range; label: string }[] = [
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 días' },
  { value: '30d', label: '30 días' },
]
const range = ref<Range>('7d')

const hours = computed(() => props.traffic?.hours ?? [])
const sums = computed(() => totals(hours.value))
const buckets = computed(() => {
  if (range.value === '24h') return byHour(hours.value, 24)
  return byDay(hours.value, range.value === '7d' ? 7 : 30)
})
const caption = computed(
  () =>
    ({
      '24h': 'Consumo por hora, últimas 24 horas',
      '7d': 'Consumo por día, últimos 7 días',
      '30d': 'Consumo por día, últimos 30 días',
    })[range.value],
)

const tiles = computed(() => [
  { label: 'Hoy', value: sums.value.today },
  { label: 'Últimos 7 días', value: sums.value.week },
  { label: 'Este mes', value: sums.value.month },
])
</script>

<template>
  <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>
  <LoadingBlock v-else-if="loading && !traffic" />
  <AlertBox
    v-else-if="traffic && !traffic.supported"
    tone="info"
    :title="`La OLT ${oltBrand(driver)} no entrega el consumo por ONU`"
  >
    Esta marca solo da los contadores del PON entero (o del puerto Ethernet de la ONU, que en estas
    ONU sale en cero). El consumo de este cliente se ve en el MikroTik o en el sistema de
    facturación.
  </AlertBox>
  <div v-else-if="traffic" class="space-y-5">
    <section class="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <div v-for="tile in tiles" :key="tile.label" class="rounded-lg border border-line p-4">
        <p class="text-sm text-muted">{{ tile.label }}</p>
        <p class="mt-1 text-xl font-semibold">{{ formatBytes(tile.value.down) }}</p>
        <p class="text-xs text-muted">
          <ArrowDown class="inline size-3" /> de bajada · <ArrowUp class="inline size-3" />
          {{ formatBytes(tile.value.up) }} de subida
        </p>
      </div>
      <div class="rounded-lg border border-line p-4">
        <p class="text-sm text-muted">Velocidad ahora</p>
        <p class="mt-1 text-xl font-semibold">{{ formatBps(traffic.down_bps) }}</p>
        <p class="text-xs text-muted">
          <ArrowUp class="inline size-3" /> {{ formatBps(traffic.up_bps) }} ·
          {{
            traffic.traffic_at
              ? `promedio de los últimos ${traffic.every_minutes} min, ${timeAgo(traffic.traffic_at)}`
              : 'sin medir todavía'
          }}
        </p>
      </div>
    </section>

    <section>
      <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h3 class="font-semibold">{{ caption }}</h3>
        <div class="flex rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
          <button
            v-for="option in RANGES"
            :key="option.value"
            type="button"
            :aria-pressed="range === option.value"
            :class="[
              'rounded-md px-3 py-1',
              range === option.value ? 'bg-subtle font-medium' : 'text-muted',
            ]"
            @click="range = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
      <TrafficChart :buckets="buckets" :caption="caption" />
      <p class="mt-3 text-xs text-muted">
        Olterra lee los contadores de la OLT cada {{ traffic.every_minutes }} minutos y guarda lo
        que bajó y subió cada hora (dos meses). Si la ONU se reinicia, su contador vuelve a cero y
        se sigue sumando desde ahí.
      </p>
    </section>
  </div>
</template>
