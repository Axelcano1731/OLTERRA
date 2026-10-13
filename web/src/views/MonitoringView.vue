<script setup lang="ts">
import {
  Activity,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  RefreshCw,
  Search,
  TriangleAlert,
  WifiOff,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  getMonitoringSummary,
  getOnuHistory,
  listAlarms,
  listOnuStatus,
  pollOlt,
  type Alarm,
  type OnuFilter,
  type OnuHistory,
  type OnuStatus,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import RxChart from '@/components/RxChart.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fold, plural, timeAgo } from '@/lib/format'
import { oltStatus } from '@/lib/labels'
import {
  ALARM_KIND,
  alarmWhere,
  duration,
  formatDbm,
  RX_LOW_WARNING,
  signalLevel,
} from '@/lib/monitoring'
import { errorText, useAsync } from '@/lib/useAsync'

const route = useRoute()
const summary = useAsync(getMonitoringSummary)
const alarms = useAsync(() => listAlarms('open'))
const onus = useAsync(() => listOnuStatus())

const oltFilter = ref(typeof route.query.olt === 'string' ? route.query.olt : '')
const stateFilter = ref<OnuFilter>('all')
const search = ref('')
const showAllAlarms = ref(false)
const polling = ref(false)
const pollError = ref<string | null>(null)

const openKey = ref<string | null>(null)
const history = shallowRef<OnuHistory | null>(null)
const historyError = ref<string | null>(null)

const oltNames = computed(
  () => new Map((summary.data.value?.olts ?? []).map((o) => [o.id, o.name] as const)),
)

function key(onu: Pick<OnuStatus, 'olt_id' | 'pon' | 'onu'>): string {
  return `${onu.olt_id}:${onu.pon}:${onu.onu}`
}

const visible = computed(() => {
  const term = fold(search.value.trim())
  return (onus.data.value ?? []).filter((onu) => {
    if (oltFilter.value && onu.olt_id !== oltFilter.value) return false
    if (stateFilter.value === 'online' && !onu.online) return false
    if (stateFilter.value === 'offline' && onu.online) return false
    if (
      stateFilter.value === 'low' &&
      !(onu.online && onu.rx_dbm !== null && onu.rx_dbm < RX_LOW_WARNING)
    ) {
      return false
    }
    if (!term) return true
    return fold(`${onu.description ?? ''} ${onu.serial ?? ''}`).includes(term)
  })
})

const shownAlarms = computed(() => {
  const rows = (alarms.data.value ?? []).filter(
    (a) => !oltFilter.value || a.olt_id === oltFilter.value,
  )
  return showAllAlarms.value ? rows : rows.slice(0, 8)
})

async function refresh(): Promise<void> {
  await Promise.all([summary.reload(), alarms.reload(), onus.reload()])
}

// Se refresca solo: el servidor sondea cada pocos minutos.
let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => void refresh(), 30_000)
})
onBeforeUnmount(() => window.clearInterval(timer))

async function pollNow(): Promise<void> {
  polling.value = true
  pollError.value = null
  try {
    const targets = (summary.data.value?.olts ?? []).filter(
      (o) => !oltFilter.value || o.id === oltFilter.value,
    )
    const results = await Promise.allSettled(targets.map((o) => pollOlt(o.id)))
    const failed = results.find((r) => r.status === 'rejected')
    if (failed && failed.status === 'rejected') pollError.value = errorText(failed.reason)
    // El sondeo tarda unos segundos en volver de la OLT.
    await new Promise((resolve) => window.setTimeout(resolve, 8000))
    await refresh()
  } finally {
    polling.value = false
  }
}

async function toggle(onu: OnuStatus): Promise<void> {
  const k = key(onu)
  if (openKey.value === k) {
    openKey.value = null
    return
  }
  openKey.value = k
  history.value = null
  historyError.value = null
  try {
    const data = await getOnuHistory(onu.olt_id, onu.pon, onu.onu)
    if (openKey.value === k) history.value = data
  } catch (caught) {
    historyError.value = errorText(caught)
  }
}

function openAlarm(alarm: Alarm): void {
  if (alarm.onu === 0) {
    oltFilter.value = alarm.olt_id
    stateFilter.value = alarm.kind === 'pon_down' ? 'offline' : 'all'
    search.value = ''
    return
  }
  const onu = onus.data.value?.find(
    (o) => o.olt_id === alarm.olt_id && o.pon === alarm.pon && o.onu === alarm.onu,
  )
  if (onu) {
    stateFilter.value = 'all'
    search.value = onu.description ?? onu.serial ?? ''
    if (openKey.value !== key(onu)) void toggle(onu)
  }
}

watch(oltFilter, () => (openKey.value = null))

const FILTERS: { value: OnuFilter; label: string }[] = [
  { value: 'all', label: 'Todas' },
  { value: 'online', label: 'En línea' },
  { value: 'offline', label: 'Caídas' },
  { value: 'low', label: 'Señal baja' },
]
</script>

<template>
  <PageHeader
    title="Monitoreo"
    :description="
      summary.data.value?.every_minutes
        ? `Estado y señal de cada ONU. Olterra consulta cada OLT cada ${summary.data.value.every_minutes} minutos y abre una alarma cuando algo se cae o la señal se sale de rango.`
        : 'Estado y señal de cada ONU.'
    "
  >
    <template #actions>
      <button
        type="button"
        class="btn-primary"
        :disabled="polling || !summary.data.value?.olts.length"
        @click="pollNow"
      >
        <LoaderCircle v-if="polling" class="size-4 animate-spin" />
        <RefreshCw v-else class="size-4" />
        {{ polling ? 'Consultando…' : 'Actualizar ahora' }}
      </button>
    </template>
  </PageHeader>

  <AlertBox v-if="pollError" tone="danger" class="mb-6">{{ pollError }}</AlertBox>
  <AlertBox v-if="summary.error.value" tone="danger" class="mb-6">
    {{ summary.error.value }}
  </AlertBox>

  <LoadingBlock v-if="summary.loading.value && !summary.data.value" />

  <EmptyState
    v-else-if="summary.data.value && summary.data.value.onus === 0"
    :icon="Activity"
    title="Todavía no hay datos de monitoreo"
    text="Olterra empieza a consultar cada OLT en cuanto responde a una consulta. Con Actualizar ahora se consulta ya."
  />

  <template v-else-if="summary.data.value">
    <!-- Cifras clave -->
    <section class="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      <div class="card p-5">
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <CircleCheck class="size-4 text-success" /> En línea
        </p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">
          {{ summary.data.value.online.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">
          de {{ summary.data.value.onus.toLocaleString('es-CO') }} ONU
        </p>
      </div>
      <button
        type="button"
        class="card p-5 text-left hover:bg-subtle"
        @click="stateFilter = 'offline'"
      >
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <WifiOff class="size-4 text-danger" /> Caídas
        </p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">
          {{ summary.data.value.offline.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">Ver cuáles</p>
      </button>
      <button type="button" class="card p-5 text-left hover:bg-subtle" @click="stateFilter = 'low'">
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <TriangleAlert class="size-4 text-warning" /> Señal baja
        </p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">
          {{ summary.data.value.low_signal.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">por debajo de {{ RX_LOW_WARNING }} dBm</p>
      </button>
      <div class="card p-5">
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <CircleAlert class="size-4 text-danger" /> Alarmas críticas
        </p>
        <p class="mt-1 text-3xl font-semibold tabular-nums">{{ summary.data.value.critical }}</p>
        <p class="text-xs text-muted">
          y {{ plural(summary.data.value.warning, 'aviso', 'avisos') }}
        </p>
      </div>
    </section>

    <!-- OLT -->
    <section class="mt-6 flex flex-wrap gap-3">
      <button
        v-for="olt in summary.data.value.olts"
        :key="olt.id"
        type="button"
        :class="[
          'card flex min-w-[14rem] flex-1 items-center gap-3 px-4 py-3 text-left hover:bg-subtle',
          oltFilter === olt.id ? 'ring-2 ring-accent' : '',
        ]"
        :aria-pressed="oltFilter === olt.id"
        @click="oltFilter = oltFilter === olt.id ? '' : olt.id"
      >
        <span class="min-w-0 flex-1">
          <span class="block truncate text-sm font-medium">{{ olt.name }}</span>
          <span class="text-xs text-muted">
            {{ olt.online }}/{{ olt.onus }} en línea
            <template v-if="olt.monitored_at"> · {{ timeAgo(olt.monitored_at) }}</template>
            <template v-else> · sin consultar</template>
          </span>
        </span>
        <StatusPill :tone="oltStatus(olt.status).tone">{{
          oltStatus(olt.status).label
        }}</StatusPill>
      </button>
    </section>

    <!-- Alarmas -->
    <section class="mt-6">
      <h2 class="mb-2 font-semibold">Alarmas abiertas</h2>
      <p v-if="shownAlarms.length === 0" class="card px-5 py-4 text-sm text-muted">
        <CircleCheck class="mr-1 inline size-4 text-success" /> Sin alarmas abiertas.
      </p>
      <ul v-else class="card divide-y divide-line">
        <li v-for="alarm in shownAlarms" :key="alarm.id">
          <button
            type="button"
            class="flex w-full items-start gap-3 px-5 py-3 text-left hover:bg-subtle"
            @click="openAlarm(alarm)"
          >
            <CircleAlert
              v-if="alarm.severity === 'critical'"
              class="mt-0.5 size-4 shrink-0 text-danger"
            />
            <TriangleAlert v-else class="mt-0.5 size-4 shrink-0 text-warning" />
            <span class="min-w-0 flex-1">
              <span class="block text-sm">
                <span class="font-medium">{{ ALARM_KIND[alarm.kind] }}</span>
                · {{ alarm.message }}
              </span>
              <span class="text-xs text-muted">
                {{ alarmWhere(alarm) }} · {{ timeAgo(alarm.started_at) }}
              </span>
            </span>
            <StatusPill :tone="alarm.severity === 'critical' ? 'danger' : 'warning'">
              {{ alarm.severity === 'critical' ? 'Crítica' : 'Aviso' }}
            </StatusPill>
          </button>
        </li>
      </ul>
      <button
        v-if="(alarms.data.value?.length ?? 0) > 8"
        type="button"
        class="mt-2 text-sm text-muted underline hover:text-ink"
        @click="showAllAlarms = !showAllAlarms"
      >
        {{ showAllAlarms ? 'Ver menos' : `Ver todas (${alarms.data.value?.length})` }}
      </button>
    </section>

    <!-- ONU -->
    <section class="mt-6">
      <div class="mb-3 flex flex-wrap items-end gap-3">
        <h2 class="mr-auto font-semibold">Clientes</h2>
        <div class="flex rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
          <button
            v-for="option in FILTERS"
            :key="option.value"
            type="button"
            :aria-pressed="stateFilter === option.value"
            :class="[
              'rounded-md px-3 py-1',
              stateFilter === option.value ? 'bg-subtle font-medium' : 'text-muted',
            ]"
            @click="stateFilter = option.value"
          >
            {{ option.label }}
          </button>
        </div>
        <label class="relative">
          <span class="sr-only">Buscar cliente o serial</span>
          <Search class="pointer-events-none absolute top-2.5 left-3 size-4 text-muted" />
          <input
            v-model="search"
            type="search"
            class="input w-56 pl-9"
            placeholder="Cliente o serial"
          />
        </label>
      </div>

      <AlertBox v-if="onus.error.value" tone="danger">{{ onus.error.value }}</AlertBox>
      <p v-else-if="visible.length === 0" class="card px-5 py-4 text-sm text-muted">
        Ninguna ONU con estos filtros.
      </p>
      <div v-else class="card overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="bg-subtle text-xs text-muted">
            <tr>
              <th class="px-3 py-2 font-medium sm:px-4">Cliente</th>
              <th class="hidden px-4 py-2 font-medium md:table-cell">Dónde</th>
              <th class="px-3 py-2 font-medium sm:px-4">Estado</th>
              <th class="px-3 py-2 font-medium sm:px-4">Señal</th>
              <th class="hidden px-4 py-2 font-medium sm:table-cell">Desde hace</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <template v-for="onu in visible.slice(0, 300)" :key="key(onu)">
              <tr
                class="cursor-pointer hover:bg-subtle"
                :aria-expanded="openKey === key(onu)"
                tabindex="0"
                @click="toggle(onu)"
                @keydown.enter="toggle(onu)"
              >
                <td class="px-3 py-2.5 sm:px-4">
                  <span class="block font-medium">{{
                    onu.description ?? `ONU ${onu.pon}/${onu.onu}`
                  }}</span>
                  <span class="text-xs text-muted md:hidden">
                    {{ oltNames.get(onu.olt_id) }} · PON {{ onu.pon }}/{{ onu.onu }}
                  </span>
                </td>
                <td class="hidden px-4 py-2.5 text-muted md:table-cell">
                  {{ oltNames.get(onu.olt_id) }} · PON {{ onu.pon }} · ONU {{ onu.onu }}
                </td>
                <td class="px-3 py-2.5 sm:px-4">
                  <StatusPill :tone="onu.online ? 'success' : 'danger'">
                    <CircleCheck v-if="onu.online" class="size-3" />
                    <WifiOff v-else class="size-3" />
                    {{ onu.phase_label }}
                  </StatusPill>
                </td>
                <td class="px-3 py-2.5 sm:px-4 tabular-nums">
                  <template v-if="onu.online">
                    <StatusPill :tone="signalLevel(onu.rx_dbm).tone">
                      {{ signalLevel(onu.rx_dbm).label }}
                    </StatusPill>
                    <span class="block text-xs whitespace-nowrap text-muted sm:ml-1.5 sm:inline">{{
                      formatDbm(onu.rx_dbm)
                    }}</span>
                  </template>
                  <span v-else class="text-muted">—</span>
                </td>
                <td class="hidden px-4 py-2.5 text-muted sm:table-cell">
                  {{ duration(onu.status_since) }}
                </td>
              </tr>
              <tr v-if="openKey === key(onu)">
                <td colspan="5" class="bg-page px-4 py-4">
                  <AlertBox v-if="historyError" tone="danger">{{ historyError }}</AlertBox>
                  <LoadingBlock v-else-if="!history" />
                  <div v-else class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_16rem]">
                    <div class="min-w-0">
                      <h3 class="mb-1 text-sm font-semibold">Señal recibida, últimos 7 días</h3>
                      <RxChart :samples="history.samples" :hours="168" />
                    </div>
                    <div class="text-sm">
                      <h3 class="mb-1 font-semibold">Datos</h3>
                      <dl class="space-y-1 text-xs">
                        <div class="flex gap-1">
                          <dt class="text-muted">Serial:</dt>
                          <dd class="font-mono">{{ history.status.serial ?? '—' }}</dd>
                        </div>
                        <div v-if="history.status.last_online_at" class="flex gap-1">
                          <dt class="text-muted">En línea por última vez:</dt>
                          <dd>{{ timeAgo(history.status.last_online_at) }}</dd>
                        </div>
                      </dl>
                      <h3 class="mt-3 mb-1 font-semibold">Alarmas recientes</h3>
                      <p v-if="history.alarms.length === 0" class="text-xs text-muted">
                        Ninguna en estos días.
                      </p>
                      <ul v-else class="space-y-1 text-xs">
                        <li v-for="alarm in history.alarms" :key="alarm.id">
                          <span class="font-medium">{{ ALARM_KIND[alarm.kind] }}</span>
                          <span class="text-muted">
                            · {{ timeAgo(alarm.started_at) }}
                            <template v-if="alarm.cleared_at">
                              · duró {{ duration(alarm.started_at, Date.parse(alarm.cleared_at)) }}
                            </template>
                            <template v-else> · sigue abierta</template>
                          </span>
                        </li>
                      </ul>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <p v-if="visible.length > 300" class="border-t border-line px-4 py-2 text-xs text-muted">
          Se muestran 300 de {{ visible.length }}: usa el buscador o los filtros.
        </p>
      </div>
    </section>
  </template>
</template>
