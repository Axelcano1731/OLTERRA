<script setup lang="ts">
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Bell,
  ChartColumn,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Cpu,
  Gauge,
  LayoutGrid,
  MonitorSmartphone,
  SearchX,
  TriangleAlert,
  Wifi,
  WifiOff,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getOlt, getOnuHistory, getOnuTraffic, type OnuStatus } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import OnuActionsBar from '@/components/onu/OnuActionsBar.vue'
import OnuDevicesTab from '@/components/onu/OnuDevicesTab.vue'
import OnuServiceTab from '@/components/onu/OnuServiceTab.vue'
import OnuTechTab from '@/components/onu/OnuTechTab.vue'
import OnuTrafficTab from '@/components/onu/OnuTrafficTab.vue'
import RxChart from '@/components/RxChart.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { oltBrand } from '@/lib/labels'
import { ALARM_KIND, duration, formatDbm, onuName, signalLevel } from '@/lib/monitoring'
import { formatBps, formatBytes, totals } from '@/lib/traffic'
import { useAsync } from '@/lib/useAsync'
import { can } from '@/session'

const props = defineProps<{ olt: string; pon: string; onu: string }>()
const route = useRoute()
const router = useRouter()

const ponN = computed(() => Number(props.pon))
const onuN = computed(() => Number(props.onu))

// 30 días de señal para la pestaña Señal; el resumen usa las últimas 24 horas.
const history = useAsync(() => getOnuHistory(props.olt, ponN.value, onuN.value, 24 * 30))
const oltInfo = useAsync(() => getOlt(props.olt))
const traffic = useAsync(() => getOnuTraffic(props.olt, ponN.value, onuN.value, 31))

const status = computed(() => history.data.value?.status)
const driver = computed(() => oltInfo.data.value?.driver ?? '')
const notFound = computed(() => /no está en el monitoreo/i.test(history.error.value ?? ''))

// Se refresca solo (el monitoreo sondea cada pocos minutos), sin parpadeo.
let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => {
    void history.reload()
    void traffic.reload()
  }, 60_000)
})
onBeforeUnmount(() => window.clearInterval(timer))

function renamed(next: OnuStatus): void {
  if (history.data.value) history.data.value = { ...history.data.value, status: next }
}

// --- Pestañas (van en la URL: ?pestana=consumo) --------------------------------------------
type Tab = 'resumen' | 'senal' | 'consumo' | 'servicio' | 'equipos' | 'alarmas' | 'tecnico'
const TABS: { id: Tab; label: string; icon: Component }[] = [
  { id: 'resumen', label: 'Resumen', icon: LayoutGrid },
  { id: 'senal', label: 'Señal', icon: Activity },
  { id: 'consumo', label: 'Consumo', icon: ChartColumn },
  { id: 'servicio', label: 'Internet y Wi‑Fi', icon: Wifi },
  { id: 'equipos', label: 'Equipos', icon: MonitorSmartphone },
  { id: 'alarmas', label: 'Alarmas', icon: Bell },
  { id: 'tecnico', label: 'Técnico', icon: Cpu },
]
const tab = computed<Tab>({
  get: () => {
    const asked = route.query.pestana
    return TABS.some((item) => item.id === asked) ? (asked as Tab) : 'resumen'
  },
  set: (next) =>
    void router.replace({
      query: { ...route.query, pestana: next === 'resumen' ? undefined : next },
    }),
})

// --- Señal ---------------------------------------------------------------------------------
const RANGES = [
  { hours: 24, label: '24 horas' },
  { hours: 168, label: '7 días' },
  { hours: 720, label: '30 días' },
]
const signalHours = ref(168)
function samplesSince(hours: number) {
  const since = Date.now() - hours * 3600_000
  return (history.data.value?.samples ?? []).filter((s) => Date.parse(s.at) >= since)
}

// --- Resumen -------------------------------------------------------------------------------
const today = computed(() => totals(traffic.data.value?.hours ?? []).today)
const openAlarms = computed(() => (history.data.value?.alarms ?? []).filter((a) => !a.cleared_at))
const recentAlarms = computed(() => (history.data.value?.alarms ?? []).slice(0, 4))
</script>

<template>
  <EmptyState
    v-if="notFound"
    :icon="SearchX"
    title="Esa ONU no está en el monitoreo"
    text="Puede que la hayan eliminado de la OLT o que todavía no la haya visto un sondeo."
  >
    <RouterLink :to="{ name: 'clients' }" class="btn-primary">Ver todos los clientes</RouterLink>
  </EmptyState>
  <AlertBox v-else-if="history.error.value && !status" tone="danger">
    {{ history.error.value }}
  </AlertBox>
  <LoadingBlock v-else-if="!status" />

  <template v-else>
    <!-- Migas: de dónde viene la ficha -->
    <nav class="mb-3 flex items-center gap-1 text-sm text-muted" aria-label="Ruta">
      <RouterLink :to="{ name: 'clients' }" class="hover:text-ink">Clientes</RouterLink>
      <ChevronRight class="size-3.5" />
      <RouterLink :to="{ name: 'clients', query: { olt: status.olt_id } }" class="hover:text-ink">
        {{ oltInfo.data.value?.name ?? 'OLT' }}
      </RouterLink>
    </nav>

    <!-- Encabezado: quién es, cómo está y qué se le puede hacer -->
    <header class="card mb-4 flex flex-wrap items-start justify-between gap-4 p-5">
      <div class="min-w-0">
        <h1 class="text-2xl font-semibold tracking-tight break-words">{{ onuName(status) }}</h1>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <StatusPill :tone="status.online ? 'success' : 'danger'">
            <CircleCheck v-if="status.online" class="size-3" />
            <WifiOff v-else class="size-3" />
            {{ status.phase_label }}
          </StatusPill>
          <StatusPill v-if="status.online" :tone="signalLevel(status.rx_dbm).tone">
            Señal {{ signalLevel(status.rx_dbm).label.toLowerCase() }} ·
            {{ formatDbm(status.rx_dbm) }}
          </StatusPill>
          <StatusPill v-if="openAlarms.length" tone="warning">
            {{ openAlarms.length }} {{ openAlarms.length === 1 ? 'alarma' : 'alarmas' }}
          </StatusPill>
        </div>
        <p class="mt-2 text-sm text-muted">
          {{ oltInfo.data.value?.name ?? 'OLT' }}
          <template v-if="driver"> ({{ oltBrand(driver) }})</template>
          · PON {{ status.pon }} · ONU {{ status.onu }}
          <template v-if="status.serial">
            · <span class="font-mono">{{ status.serial }}</span>
          </template>
        </p>
        <p
          v-if="status.customer_name && status.description"
          class="text-xs text-muted"
          title="La descripción que tiene la ONU en la OLT"
        >
          En la OLT: <span class="font-mono">{{ status.description }}</span>
        </p>
      </div>
      <OnuActionsBar v-if="can('onu:write')" :status="status" :driver="driver" @renamed="renamed" />
    </header>

    <!-- Pestañas -->
    <div class="mb-4 overflow-x-auto border-b border-line">
      <div class="flex min-w-max gap-1" role="tablist" aria-label="Ficha del cliente">
        <button
          v-for="item in TABS"
          :id="`pestana-${item.id}`"
          :key="item.id"
          type="button"
          role="tab"
          :aria-selected="tab === item.id"
          :class="[
            '-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors',
            tab === item.id
              ? 'border-accent text-ink'
              : 'border-transparent text-muted hover:text-ink',
          ]"
          @click="tab = item.id"
        >
          <component :is="item.icon" class="size-4" />
          {{ item.label }}
        </button>
      </div>
    </div>

    <div role="tabpanel" :aria-labelledby="`pestana-${tab}`">
      <!-- Resumen -->
      <div v-if="tab === 'resumen'" class="space-y-4">
        <section class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div class="card p-4">
            <p class="flex items-center gap-1.5 text-sm text-muted">
              <CircleCheck v-if="status.online" class="size-4 text-success" />
              <WifiOff v-else class="size-4 text-danger" />
              Estado
            </p>
            <p class="mt-1 text-xl font-semibold">{{ status.online ? 'En línea' : 'Caída' }}</p>
            <p class="text-xs text-muted">
              desde hace {{ duration(status.status_since) }}
              <template v-if="!status.online && status.last_online_at">
                · en línea {{ timeAgo(status.last_online_at) }}
              </template>
            </p>
          </div>
          <button type="button" class="card p-4 text-left hover:bg-subtle" @click="tab = 'senal'">
            <p class="flex items-center gap-1.5 text-sm text-muted">
              <Activity class="size-4" /> Señal
            </p>
            <p class="mt-1 text-xl font-semibold">
              {{ status.online ? formatDbm(status.rx_dbm) : '—' }}
            </p>
            <p class="text-xs text-muted">
              {{ status.online ? signalLevel(status.rx_dbm).label : 'sin lectura' }} · ver historial
            </p>
          </button>
          <button type="button" class="card p-4 text-left hover:bg-subtle" @click="tab = 'consumo'">
            <p class="flex items-center gap-1.5 text-sm text-muted">
              <ChartColumn class="size-4" /> Consumo hoy
            </p>
            <template v-if="traffic.data.value?.supported">
              <p class="mt-1 text-xl font-semibold">{{ formatBytes(today.down) }}</p>
              <p class="text-xs text-muted">
                <ArrowDown class="inline size-3" /> de bajada · <ArrowUp class="inline size-3" />
                {{ formatBytes(today.up) }} de subida
              </p>
            </template>
            <template v-else>
              <p class="mt-1 text-xl font-semibold">—</p>
              <p class="text-xs text-muted">
                {{ traffic.data.value ? 'esta OLT no lo entrega' : 'cargando…' }}
              </p>
            </template>
          </button>
          <div class="card p-4">
            <p class="flex items-center gap-1.5 text-sm text-muted">
              <Gauge class="size-4" /> Velocidad ahora
            </p>
            <p class="mt-1 text-xl font-semibold">{{ formatBps(status.down_bps) }}</p>
            <p class="text-xs text-muted">
              <ArrowUp class="inline size-3" /> {{ formatBps(status.up_bps) }}
              <template v-if="status.traffic_at"> · {{ timeAgo(status.traffic_at) }}</template>
            </p>
          </div>
        </section>

        <section class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div class="card min-w-0 p-5">
            <div class="mb-2 flex items-center justify-between gap-2">
              <h2 class="font-semibold">Señal, últimas 24 horas</h2>
              <button
                type="button"
                class="text-sm text-muted underline hover:text-ink"
                @click="tab = 'senal'"
              >
                Ver más días
              </button>
            </div>
            <RxChart :samples="samplesSince(24)" :hours="24" />
          </div>
          <div class="card p-5">
            <div class="mb-2 flex items-center justify-between gap-2">
              <h2 class="font-semibold">Alarmas recientes</h2>
              <button
                v-if="history.data.value?.alarms.length"
                type="button"
                class="text-sm text-muted underline hover:text-ink"
                @click="tab = 'alarmas'"
              >
                Todas
              </button>
            </div>
            <p v-if="!recentAlarms.length" class="text-sm text-muted">
              <CircleCheck class="mr-1 inline size-4 text-success" /> Ninguna en estos días.
            </p>
            <ul v-else class="space-y-2 text-sm">
              <li v-for="alarm in recentAlarms" :key="alarm.id" class="flex gap-2">
                <CircleAlert
                  v-if="alarm.severity === 'critical'"
                  class="mt-0.5 size-4 shrink-0 text-danger"
                />
                <TriangleAlert v-else class="mt-0.5 size-4 shrink-0 text-warning" />
                <span>
                  <span class="font-medium">{{ ALARM_KIND[alarm.kind] }}</span>
                  <span class="block text-xs text-muted">
                    {{ timeAgo(alarm.started_at) }} ·
                    {{
                      alarm.cleared_at
                        ? `duró ${duration(alarm.started_at, Date.parse(alarm.cleared_at))}`
                        : 'sigue abierta'
                    }}
                  </span>
                </span>
              </li>
            </ul>
          </div>
        </section>
      </div>

      <!-- Señal -->
      <section v-else-if="tab === 'senal'" class="card p-5">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 class="font-semibold">Señal recibida</h2>
          <div class="flex rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
            <button
              v-for="option in RANGES"
              :key="option.hours"
              type="button"
              :aria-pressed="signalHours === option.hours"
              :class="[
                'rounded-md px-3 py-1',
                signalHours === option.hours ? 'bg-subtle font-medium' : 'text-muted',
              ]"
              @click="signalHours = option.hours"
            >
              {{ option.label }}
            </button>
          </div>
        </div>
        <RxChart :samples="samplesSince(signalHours)" :hours="signalHours" />
        <p class="mt-3 text-xs text-muted">
          Una muestra por hora, o antes si la señal cambia 1 dB o la ONU se cae o vuelve. Ventana
          GPON: aviso desde −25 dBm y crítico desde −27 dBm.
        </p>
      </section>

      <!-- Consumo -->
      <section v-else-if="tab === 'consumo'" class="card p-5">
        <OnuTrafficTab
          :traffic="traffic.data.value"
          :loading="traffic.loading.value"
          :error="traffic.error.value"
          :driver="driver"
        />
      </section>

      <!-- Internet y Wi‑Fi -->
      <OnuServiceTab v-else-if="tab === 'servicio' && driver" :status="status" :driver="driver" />

      <!-- Equipos -->
      <section v-else-if="tab === 'equipos'" class="card p-5">
        <OnuDevicesTab :status="status" />
      </section>

      <!-- Alarmas -->
      <section v-else-if="tab === 'alarmas'" class="card overflow-hidden">
        <p v-if="!history.data.value?.alarms.length" class="p-5 text-sm text-muted">
          <CircleCheck class="mr-1 inline size-4 text-success" /> Ninguna alarma en los últimos 30
          días.
        </p>
        <ul v-else class="divide-y divide-line">
          <li
            v-for="alarm in history.data.value.alarms"
            :key="alarm.id"
            class="flex items-start gap-3 px-5 py-3"
          >
            <CircleAlert
              v-if="alarm.severity === 'critical'"
              class="mt-0.5 size-4 shrink-0 text-danger"
            />
            <TriangleAlert v-else class="mt-0.5 size-4 shrink-0 text-warning" />
            <span class="min-w-0 flex-1 text-sm">
              <span class="font-medium">{{ ALARM_KIND[alarm.kind] }}</span>
              · {{ alarm.message }}
              <span class="block text-xs text-muted">
                {{ formatDateTime(alarm.started_at) }} ·
                {{
                  alarm.cleared_at
                    ? `duró ${duration(alarm.started_at, Date.parse(alarm.cleared_at))}`
                    : 'sigue abierta'
                }}
              </span>
            </span>
            <StatusPill :tone="alarm.cleared_at ? 'neutral' : 'warning'">
              {{ alarm.cleared_at ? 'Cerrada' : 'Abierta' }}
            </StatusPill>
          </li>
        </ul>
      </section>

      <!-- Técnico -->
      <section v-else-if="tab === 'tecnico' && driver">
        <OnuTechTab :status="status" :driver="driver" />
      </section>
    </div>
  </template>
</template>
