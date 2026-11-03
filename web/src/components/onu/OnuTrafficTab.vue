<script setup lang="ts">
import { ArrowDown, ArrowUp, LoaderCircle, Pencil } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

import { setOnuPlan, type OnuStatus, type OnuTraffic } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import RateChart from '@/components/RateChart.vue'
import TrafficChart from '@/components/TrafficChart.vue'
import { timeAgo } from '@/lib/format'
import { oltBrand } from '@/lib/labels'
import {
  averageEvery,
  byDay,
  byHour,
  formatBps,
  formatBytes,
  hourlyRates,
  rateStats,
  totals,
  type RatePoint,
} from '@/lib/traffic'
import { errorText } from '@/lib/useAsync'
import { can } from '@/session'

const props = defineProps<{
  traffic: OnuTraffic | undefined
  loading: boolean
  error: string | null
  driver: string
  status: OnuStatus
}>()

type Range = '24h' | '7d' | '30d'
const RANGES: { value: Range; label: string }[] = [
  { value: '24h', label: '24 horas' },
  { value: '7d', label: '7 días' },
  { value: '30d', label: '30 días' },
]
const view = ref<'speed' | 'volume'>('speed')
const range = ref<Range>('24h')

const hours = computed(() => props.traffic?.hours ?? [])
const sums = computed(() => totals(hours.value))

// --- Velocidad: como MRTG, 5 min el día, 30 min la semana y 2 horas el mes -----------------
const H = 3600_000
const speed = computed(() => {
  const end = Date.now()
  const rates: RatePoint[] = (props.traffic?.rates ?? []).map((r) => ({
    t: Date.parse(r.at),
    down: r.down_bps,
    up: r.up_bps,
  }))
  if (range.value === '24h') {
    const start = end - 24 * H
    return { start, end, gapMs: 16 * 60_000, points: rates.filter((p) => p.t >= start), every: 5 }
  }
  if (range.value === '7d') {
    const start = end - 7 * 24 * H
    const points = averageEvery(
      rates.filter((p) => p.t >= start),
      30,
    )
    return { start, end, gapMs: 2 * H, points, every: 30 }
  }
  const start = end - 30 * 24 * H
  const points = averageEvery(
    hourlyRates(hours.value).filter((p) => p.t >= start),
    120,
  )
  return { start, end, gapMs: 7 * H, points, every: 120 }
})

const plan = ref<number | null>(props.status.plan_mbps ?? props.traffic?.plan_mbps ?? null)
watch(
  () => props.traffic?.plan_mbps,
  (value) => {
    if (value !== undefined) plan.value = value
  },
)
const planBps = computed(() => (plan.value ? plan.value * 1e6 : null))
const stats = computed(() => rateStats(speed.value.points, planBps.value))

const editingPlan = ref(false)
const planDraft = ref<number | null>(null)
const savingPlan = ref(false)
const planError = ref<string | null>(null)

function editPlan(): void {
  planDraft.value = plan.value
  planError.value = null
  editingPlan.value = true
}

async function savePlan(): Promise<void> {
  savingPlan.value = true
  planError.value = null
  try {
    const value = planDraft.value && planDraft.value > 0 ? Math.round(planDraft.value) : null
    const saved = await setOnuPlan(props.status.olt_id, props.status.pon, props.status.onu, value)
    plan.value = saved.plan_mbps ?? null
    editingPlan.value = false
  } catch (caught) {
    planError.value = errorText(caught)
  } finally {
    savingPlan.value = false
  }
}

// --- Volumen: lo que bajó y subió por hora o por día -----------------------------------------
const buckets = computed(() => {
  if (range.value === '24h') return byHour(hours.value, 24)
  return byDay(hours.value, range.value === '7d' ? 7 : 30)
})

const caption = computed(() => {
  const period = { '24h': 'últimas 24 horas', '7d': 'últimos 7 días', '30d': 'últimos 30 días' }[
    range.value
  ]
  if (view.value === 'speed') {
    return `Velocidad (promedio de ${speed.value.every < 60 ? `${speed.value.every} min` : `${speed.value.every / 60} h`}), ${period}`
  }
  return `Consumo por ${range.value === '24h' ? 'hora' : 'día'}, ${period}`
})

const tiles = computed(() => [
  { label: 'Hoy', value: sums.value.today },
  { label: 'Últimos 7 días', value: sums.value.week },
  { label: 'Este mes', value: sums.value.month },
])

function percent(value: number): string {
  return `${Math.round(value * 100)} %`
}
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
        <p class="text-sm text-muted">Plan del cliente</p>
        <form v-if="editingPlan" class="mt-1 flex items-center gap-1.5" @submit.prevent="savePlan">
          <input
            v-model.number="planDraft"
            type="number"
            min="1"
            max="10000"
            class="input w-20 py-1"
            aria-label="Velocidad del plan en Mbps"
          />
          <span class="text-sm text-muted">Mbps</span>
          <button type="submit" class="btn-primary px-2 py-1 text-xs" :disabled="savingPlan">
            <LoaderCircle v-if="savingPlan" class="size-3 animate-spin" />
            Guardar
          </button>
        </form>
        <p v-else class="mt-1 flex items-center gap-2 text-xl font-semibold">
          {{ plan ? `${plan} Mbps` : '—' }}
          <button
            v-if="can('onu:write')"
            type="button"
            class="btn-ghost px-1.5 py-0.5 text-xs font-normal"
            @click="editPlan"
          >
            <Pencil class="size-3" />
            {{ plan ? 'Cambiar' : 'Poner' }}
          </button>
        </p>
        <p v-if="planError" class="text-xs text-danger">{{ planError }}</p>
        <p v-else class="text-xs text-muted">
          {{ plan ? 'la raya de la gráfica' : 'para ver si llega al tope' }}
        </p>
      </div>
    </section>

    <section>
      <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div class="flex rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
          <button
            type="button"
            :aria-pressed="view === 'speed'"
            :class="[
              'rounded-md px-3 py-1',
              view === 'speed' ? 'bg-subtle font-medium' : 'text-muted',
            ]"
            @click="view = 'speed'"
          >
            Velocidad
          </button>
          <button
            type="button"
            :aria-pressed="view === 'volume'"
            :class="[
              'rounded-md px-3 py-1',
              view === 'volume' ? 'bg-subtle font-medium' : 'text-muted',
            ]"
            @click="view = 'volume'"
          >
            Consumo (GB)
          </button>
        </div>
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
      <h3 class="mb-2 font-semibold">{{ caption }}</h3>

      <template v-if="view === 'speed'">
        <RateChart
          :points="speed.points"
          :start="speed.start"
          :end="speed.end"
          :gap-ms="speed.gapMs"
          :plan-bps="planBps"
          :caption="caption"
        />
        <div class="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted">
          <span class="flex items-center gap-1.5">
            <span class="inline-block size-2.5 rounded-sm bg-[var(--color-chart-down)]" /> Bajada
          </span>
          <span class="flex items-center gap-1.5">
            <span class="inline-block h-0.5 w-4 rounded bg-[var(--color-chart-up)]" /> Subida
          </span>
          <span v-if="planBps" class="flex items-center gap-1.5">
            <span class="inline-block h-px w-4 bg-ink/55" /> Plan
          </span>
        </div>
        <dl
          v-if="stats"
          class="mt-3 grid gap-x-6 gap-y-1 rounded-lg bg-subtle px-4 py-3 text-xs sm:grid-cols-2 lg:grid-cols-4"
        >
          <div>
            <dt class="text-muted">Máximo</dt>
            <dd class="tabular-nums">
              ↓ {{ formatBps(stats.max.down) }} · ↑ {{ formatBps(stats.max.up) }}
            </dd>
          </div>
          <div>
            <dt class="text-muted">Promedio</dt>
            <dd class="tabular-nums">
              ↓ {{ formatBps(stats.average.down) }} · ↑ {{ formatBps(stats.average.up) }}
            </dd>
          </div>
          <div>
            <dt class="text-muted">Actual</dt>
            <dd class="tabular-nums">
              ↓ {{ formatBps(stats.current.down) }} · ↑ {{ formatBps(stats.current.up) }}
              <template v-if="traffic.traffic_at"> · {{ timeAgo(traffic.traffic_at) }}</template>
            </dd>
          </div>
          <div>
            <dt class="text-muted">Al tope del plan</dt>
            <dd>
              <template v-if="stats.atPlan !== null">
                {{ percent(stats.atPlan) }} del tiempo (al 90 % o más)
              </template>
              <template v-else>pon el plan para saberlo</template>
            </dd>
          </div>
        </dl>
        <p class="mt-3 text-xs text-muted">
          Lo que pasa por la ONU según la OLT, leído cada {{ traffic.every_minutes }} minutos (como
          MRTG): un cliente que siempre llega a la raya del plan está usando todo su canal. Los
          huecos son ratos en que la OLT no respondió o la ONU estuvo caída.
        </p>
      </template>
      <template v-else>
        <TrafficChart :buckets="buckets" :caption="caption" />
        <p class="mt-3 text-xs text-muted">
          Lo que bajó y subió, sumado por hora (dos meses). Si la ONU se reinicia, su contador
          vuelve a cero y se sigue sumando desde ahí.
        </p>
      </template>
    </section>
  </div>
</template>
