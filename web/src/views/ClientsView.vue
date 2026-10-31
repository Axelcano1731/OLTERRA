<script setup lang="ts">
import {
  Activity,
  ArrowDown,
  Bell,
  CircleCheck,
  LoaderCircle,
  PlugZap,
  RefreshCw,
  Search,
  TriangleAlert,
  WifiOff,
} from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getMonitoringSummary, listOnuStatus, pollOlt, type OnuFilter, type OnuStatus } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fold, plural, timeAgo } from '@/lib/format'
import { oltStatus } from '@/lib/labels'
import { duration, formatDbm, onuName, RX_LOW_WARNING, signalLevel } from '@/lib/monitoring'
import { formatBps } from '@/lib/traffic'
import { errorText, useAsync } from '@/lib/useAsync'

const route = useRoute()
const router = useRouter()
const summary = useAsync(getMonitoringSummary)
const onus = useAsync(() => listOnuStatus())

// Los filtros van en la URL: el Inicio y las alarmas llegan aquí ya filtrados.
const oltFilter = ref(typeof route.query.olt === 'string' ? route.query.olt : '')
const stateFilter = ref<OnuFilter>(
  ['online', 'offline', 'low'].includes(String(route.query.estado))
    ? (route.query.estado as OnuFilter)
    : 'all',
)
const search = ref(typeof route.query.q === 'string' ? route.query.q : '')
const limit = ref(200)

watch([oltFilter, stateFilter, search], ([olt, estado, q]) => {
  limit.value = 200
  void router.replace({
    query: {
      olt: olt || undefined,
      estado: estado === 'all' ? undefined : estado,
      q: q || undefined,
    },
  })
})

const oltNames = computed(
  () => new Map((summary.data.value?.olts ?? []).map((o) => [o.id, o.name] as const)),
)

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
    const place = `${onu.pon}/${onu.onu}`
    return (
      place === term ||
      fold(`${onu.customer_name ?? ''} ${onu.description ?? ''} ${onu.serial ?? ''}`).includes(term)
    )
  })
})

function open(onu: OnuStatus): void {
  void router.push(link(onu))
}

function link(onu: OnuStatus) {
  return {
    name: 'onu',
    params: { olt: onu.olt_id, pon: String(onu.pon), onu: String(onu.onu) },
  }
}

// Se refresca solo: el servidor sondea cada pocos minutos.
let timer: number | undefined
onMounted(() => {
  timer = window.setInterval(() => void Promise.all([summary.reload(), onus.reload()]), 30_000)
})
onBeforeUnmount(() => window.clearInterval(timer))

const polling = ref(false)
const pollError = ref<string | null>(null)
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
    await Promise.all([summary.reload(), onus.reload()])
  } finally {
    polling.value = false
  }
}

const FILTERS: { value: OnuFilter; label: string }[] = [
  { value: 'all', label: 'Todos' },
  { value: 'online', label: 'En línea' },
  { value: 'offline', label: 'Caídos' },
  { value: 'low', label: 'Señal baja' },
]
</script>

<template>
  <PageHeader
    title="Clientes"
    description="Cada ONU con su estado, su señal y su consumo. Ábrela para ver su historial, cambiar el Wi‑Fi o el PPPoE, renombrarla, reiniciarla o eliminarla."
  >
    <template #actions>
      <button
        type="button"
        class="btn-secondary"
        :disabled="polling || !summary.data.value?.olts.length"
        @click="pollNow"
      >
        <LoaderCircle v-if="polling" class="size-4 animate-spin" />
        <RefreshCw v-else class="size-4" />
        {{ polling ? 'Consultando…' : 'Actualizar ahora' }}
      </button>
      <RouterLink :to="{ name: 'provision' }" class="btn-primary">
        <PlugZap class="size-4" />
        Autorizar ONU
      </RouterLink>
    </template>
  </PageHeader>

  <AlertBox v-if="pollError" tone="danger" class="mb-4">{{ pollError }}</AlertBox>
  <AlertBox v-if="summary.error.value" tone="danger" class="mb-4">
    {{ summary.error.value }}
  </AlertBox>

  <LoadingBlock v-if="summary.loading.value && !summary.data.value" />

  <EmptyState
    v-else-if="summary.data.value && summary.data.value.onus === 0"
    :icon="Activity"
    title="Todavía no hay clientes en el monitoreo"
    text="Olterra empieza a consultar cada OLT en cuanto responde. Con Actualizar ahora se consulta ya."
  />

  <template v-else-if="summary.data.value">
    <!-- Cifras clave: cada una filtra la lista -->
    <section class="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <button
        type="button"
        :class="[
          'card p-4 text-left hover:bg-subtle',
          stateFilter === 'online' && 'ring-2 ring-accent',
        ]"
        @click="stateFilter = stateFilter === 'online' ? 'all' : 'online'"
      >
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <CircleCheck class="size-4 text-success" /> En línea
        </p>
        <p class="mt-1 text-2xl font-semibold">
          {{ summary.data.value.online.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">de {{ summary.data.value.onus.toLocaleString('es-CO') }}</p>
      </button>
      <button
        type="button"
        :class="[
          'card p-4 text-left hover:bg-subtle',
          stateFilter === 'offline' && 'ring-2 ring-accent',
        ]"
        @click="stateFilter = stateFilter === 'offline' ? 'all' : 'offline'"
      >
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <WifiOff class="size-4 text-danger" /> Caídos
        </p>
        <p class="mt-1 text-2xl font-semibold">
          {{ summary.data.value.offline.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">ver cuáles</p>
      </button>
      <button
        type="button"
        :class="[
          'card p-4 text-left hover:bg-subtle',
          stateFilter === 'low' && 'ring-2 ring-accent',
        ]"
        @click="stateFilter = stateFilter === 'low' ? 'all' : 'low'"
      >
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <TriangleAlert class="size-4 text-warning" /> Señal baja
        </p>
        <p class="mt-1 text-2xl font-semibold">
          {{ summary.data.value.low_signal.toLocaleString('es-CO') }}
        </p>
        <p class="text-xs text-muted">por debajo de {{ RX_LOW_WARNING }} dBm</p>
      </button>
      <RouterLink :to="{ name: 'alarms' }" class="card p-4 hover:bg-subtle">
        <p class="flex items-center gap-1.5 text-sm text-muted">
          <Bell class="size-4 text-danger" /> Alarmas abiertas
        </p>
        <p class="mt-1 text-2xl font-semibold">
          {{ summary.data.value.critical + summary.data.value.warning }}
        </p>
        <p class="text-xs text-muted">
          {{ plural(summary.data.value.critical, 'crítica', 'críticas') }} · ver todas
        </p>
      </RouterLink>
    </section>

    <!-- OLT: un toque filtra -->
    <section v-if="summary.data.value.olts.length > 1" class="mt-4 flex flex-wrap gap-2">
      <button
        v-for="olt in summary.data.value.olts"
        :key="olt.id"
        type="button"
        :class="[
          'card flex min-w-[13rem] flex-1 items-center gap-3 px-4 py-2.5 text-left hover:bg-subtle',
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
          </span>
        </span>
        <StatusPill :tone="oltStatus(olt.status).tone">{{
          oltStatus(olt.status).label
        }}</StatusPill>
      </button>
    </section>

    <!-- Lista -->
    <section class="mt-4">
      <div class="mb-3 flex flex-wrap items-center gap-3">
        <label class="relative min-w-[14rem] flex-1">
          <span class="sr-only">Buscar cliente, serial o posición</span>
          <Search
            class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted"
          />
          <input
            v-model="search"
            type="search"
            class="input pl-9"
            placeholder="Buscar por nombre, serial o posición (1/12)"
          />
        </label>
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
      </div>

      <AlertBox v-if="onus.error.value" tone="danger">{{ onus.error.value }}</AlertBox>
      <p v-else-if="visible.length === 0" class="card px-5 py-4 text-sm text-muted">
        Ningún cliente con estos filtros.
      </p>
      <div v-else class="card overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="bg-subtle text-xs text-muted">
            <tr>
              <th class="px-3 py-2 font-medium sm:px-4">Cliente</th>
              <th class="hidden px-4 py-2 font-medium md:table-cell">Dónde</th>
              <th class="px-3 py-2 font-medium sm:px-4">Estado</th>
              <th class="px-3 py-2 font-medium sm:px-4">Señal</th>
              <th class="hidden px-4 py-2 font-medium lg:table-cell">Consumo ahora</th>
              <th class="hidden px-4 py-2 font-medium sm:table-cell">Desde hace</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr
              v-for="onu in visible.slice(0, limit)"
              :key="`${onu.olt_id}:${onu.pon}:${onu.onu}`"
              class="cursor-pointer hover:bg-subtle"
              @click="open(onu)"
            >
              <td class="px-3 py-2.5 sm:px-4">
                <RouterLink :to="link(onu)" class="block font-medium hover:underline" @click.stop>
                  {{ onuName(onu) }}
                </RouterLink>
                <span class="text-xs text-muted">
                  <span class="md:hidden">
                    {{ oltNames.get(onu.olt_id) }} · {{ onu.pon }}/{{ onu.onu }} ·
                  </span>
                  <span class="font-mono">{{ onu.serial ?? '' }}</span>
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
              <td class="px-3 py-2.5 tabular-nums sm:px-4">
                <template v-if="onu.online">
                  <StatusPill :tone="signalLevel(onu.rx_dbm).tone">
                    {{ signalLevel(onu.rx_dbm).label }}
                  </StatusPill>
                  <span class="block text-xs whitespace-nowrap text-muted sm:ml-1.5 sm:inline">
                    {{ formatDbm(onu.rx_dbm) }}
                  </span>
                </template>
                <span v-else class="text-muted">—</span>
              </td>
              <td class="hidden px-4 py-2.5 whitespace-nowrap tabular-nums lg:table-cell">
                <template v-if="onu.online && onu.down_bps !== null && onu.down_bps !== undefined">
                  <ArrowDown class="inline size-3 text-muted" /> {{ formatBps(onu.down_bps) }}
                </template>
                <span v-else class="text-muted">—</span>
              </td>
              <td class="hidden px-4 py-2.5 text-muted sm:table-cell">
                {{ duration(onu.status_since) }}
              </td>
            </tr>
          </tbody>
        </table>
        <button
          v-if="visible.length > limit"
          type="button"
          class="w-full border-t border-line px-4 py-2 text-sm text-muted hover:bg-subtle hover:text-ink"
          @click="limit += 200"
        >
          Ver más ({{ visible.length - limit }})
        </button>
      </div>
    </section>
  </template>
</template>
