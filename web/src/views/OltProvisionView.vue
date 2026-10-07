<script setup lang="ts">
import { ChevronDown, ChevronUp, LayoutTemplate, RefreshCw, Search, Users } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  getOlt,
  listJobs,
  listOlts,
  listTemplates,
  scanOnus,
  type AutofindRow,
  type InterfacesBrief,
  type OnuPort,
  type ProvisionJob,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import JobProgress from '@/components/JobProgress.vue'
import NewOnuCard from '@/components/NewOnuCard.vue'
import OnuActions from '@/components/OnuActions.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fold } from '@/lib/format'
import { errorText, useAsync } from '@/lib/useAsync'
import { usePlan } from '@/lib/usePlan'

const props = defineProps<{ id: string }>()
const router = useRouter()

const olt = useAsync(() => getOlt(props.id))
const olts = useAsync(listOlts)
const plans = useAsync(listTemplates)

// Últimas altas: lo que se hizo (o sigue en curso) aunque la ONU ya no esté en "nuevas".
const recent = useAsync(() => listJobs(props.id))
const openJob = ref<string | null>(null)
let recentTimer: number | undefined
watch(
  () => recent.data.value,
  (jobs) => {
    window.clearTimeout(recentTimer)
    if (jobs?.some((job) => job.status === 'running')) {
      recentTimer = window.setTimeout(() => void recent.reload(), 3000)
    }
  },
)
onBeforeUnmount(() => window.clearTimeout(recentTimer))

// --- Una sola lectura: ONU nuevas en todos los PON + la lista de clientes -------------------
const scan = usePlan()
const scanError = ref<string | null>(null)
let rescanned = false

async function refresh(): Promise<void> {
  scanError.value = null
  try {
    const plan = await scanOnus(props.id)
    await scan.follow(plan.plan_id)
  } catch (caught) {
    scanError.value = errorText(caught)
  }
}

onMounted(refresh)
watch(
  () => props.id,
  () => {
    rescanned = false
    void olt.reload()
    void refresh()
  },
)

const outputs = computed(() => scan.plan.value?.result?.outputs ?? [])

const brief = computed<InterfacesBrief | null>(() => {
  const output = outputs.value.find((item) => item.key === 'interfaces.brief')
  return output?.ok && output.data ? (output.data as InterfacesBrief) : null
})

const newOnus = computed(() =>
  outputs.value
    .filter((item) => item.key === 'onu.autofind' && item.ok && Array.isArray(item.data))
    .flatMap((item) =>
      (item.data as AutofindRow[]).map((row) => ({
        ...row,
        pon: row.pon ?? Number(item.params.pon),
      })),
    ),
)

const unreadPons = computed(() =>
  outputs.value
    .filter((item) => item.key === 'onu.autofind' && (!item.ok || item.parse_error))
    .map((item) => Number(item.params.pon)),
)

// La primera vez se buscan 4 PON; si la OLT tiene más, la lectura lo dice y se busca de nuevo.
watch(brief, (value) => {
  if (!value || rescanned) return
  const scanned = new Set(
    outputs.value
      .filter((item) => item.key === 'onu.autofind')
      .map((item) => Number(item.params.pon)),
  )
  if (value.pons.some((pon) => !scanned.has(pon))) {
    rescanned = true
    void refresh()
  }
})

// --- Clientes de la OLT -------------------------------------------------------------------
const query = ref('')
const limit = ref(40)
const openRow = ref<string | null>(null)

const customers = computed<OnuPort[]>(() => {
  const all = [...(brief.value?.onus ?? [])].sort((a, b) => a.pon - b.pon || a.onu - b.onu)
  const text = fold(query.value.trim())
  if (!text) return all
  return all.filter((row) => {
    const place = `${row.pon}/${row.onu}`
    return fold(row.description ?? '').includes(text) || place === text.replace(':', '/')
  })
})

const offline = computed(() => (brief.value?.onus ?? []).filter((row) => !row.up).length)

function rowKey(row: OnuPort): string {
  return `${row.pon}/${row.onu}`
}

function onFinished(job?: ProvisionJob): void {
  if (job) openJob.value = job.id // el resultado queda abierto en "Últimas altas"
  void recent.reload()
  void refresh()
}

const JOB_STATUS = {
  running: { label: 'En curso', tone: 'accent' },
  done: { label: 'Listo', tone: 'success' },
  failed: { label: 'No terminó', tone: 'danger' },
} as const

function switchOlt(event: Event): void {
  const id = (event.target as HTMLSelectElement).value
  if (id && id !== props.id) void router.push({ name: 'olt-provision', params: { id } })
}
</script>

<template>
  <PageHeader
    title="Aprovisionar"
    :description="
      olt.data.value
        ? `${olt.data.value.name}: conecta la ONU, ponle el nombre del cliente y su plan. Olterra hace lo demás.`
        : 'Conecta la ONU, ponle el nombre del cliente y su plan. Olterra hace lo demás.'
    "
  >
    <template #actions>
      <select
        v-if="(olts.data.value?.length ?? 0) > 1"
        class="input w-auto"
        :value="id"
        aria-label="OLT"
        @change="switchOlt"
      >
        <option v-for="item in olts.data.value ?? []" :key="item.id" :value="item.id">
          {{ item.name }}
        </option>
      </select>
      <button type="button" class="btn-secondary" :disabled="scan.waiting.value" @click="refresh">
        <RefreshCw :class="['size-4', scan.waiting.value ? 'animate-spin' : '']" />
        Actualizar
      </button>
    </template>
  </PageHeader>

  <AlertBox v-if="scanError || scan.error.value" tone="danger" class="mb-6">
    {{ scanError ?? scan.error.value }}
  </AlertBox>
  <AlertBox
    v-else-if="scan.plan.value && !scan.waiting.value && !brief"
    tone="danger"
    class="mb-6"
    title="No se pudo leer la OLT"
  >
    {{
      scan.plan.value.result?.error ?? 'La OLT no respondió. Revisa el túnel y vuelve a intentar.'
    }}
  </AlertBox>

  <AlertBox
    v-if="plans.data.value && plans.data.value.length === 0"
    tone="info"
    class="mb-6"
    title="Primero, un plan"
  >
    Un plan dice cómo se configura cada cliente (VLAN, velocidad, si la ONU marca PPPoE). La forma
    más rápida: en la lista de clientes de abajo, elige uno que ya navega y pulsa
    <b>Copiar como plan</b>. O
    <RouterLink :to="{ name: 'template-new' }" class="font-medium underline"
      >créalo a mano</RouterLink
    >.
  </AlertBox>

  <div class="space-y-8">
    <section>
      <div class="mb-3 flex items-baseline justify-between gap-3">
        <h2 class="text-lg font-semibold">ONU nuevas</h2>
        <span v-if="brief" class="text-sm text-muted">
          {{ newOnus.length }} esperando · {{ brief.pons.length }} PON
        </span>
      </div>
      <div v-if="scan.waiting.value && !newOnus.length" class="card p-6 text-sm text-muted">
        Buscando ONU nuevas en la OLT{{ scan.slow.value ? ' (está tardando)' : '' }}…
      </div>
      <div v-else-if="brief && !newOnus.length" class="card p-6 text-sm text-muted">
        No hay ONU nuevas conectadas. Conecta la ONU a la fibra, espera un minuto y pulsa
        <b>Actualizar</b>.
      </div>
      <ul v-else class="space-y-3">
        <NewOnuCard
          v-for="row in newOnus"
          :key="row.serial"
          :olt-id="id"
          :row="row"
          :plans="plans.data.value ?? []"
          @finished="onFinished"
        />
      </ul>
      <p v-if="unreadPons.length" class="mt-2 text-xs text-muted">
        No se pudo leer el PON {{ unreadPons.join(', ') }}.
      </p>
    </section>

    <section v-if="recent.data.value?.length">
      <h2 class="mb-3 text-lg font-semibold">Últimas altas</h2>
      <ul class="card divide-y divide-line">
        <li v-for="job in recent.data.value.slice(0, 6)" :key="job.id">
          <button
            type="button"
            class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle"
            :aria-expanded="openJob === job.id"
            @click="openJob = openJob === job.id ? null : job.id"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium">
                {{ job.description ?? `PON ${job.pon} · posición ${job.onu}` }}
              </span>
              <span class="text-xs text-muted">
                {{ job.kind === 'authorize' ? 'Alta' : 'Internet y WiFi' }}
                <template v-if="job.onu"> · PON {{ job.pon }}, posición {{ job.onu }}</template>
                <template v-if="job.rx_dbm !== null"> · señal {{ job.rx_dbm }} dBm</template>
              </span>
            </span>
            <StatusPill :tone="JOB_STATUS[job.status].tone">
              {{ JOB_STATUS[job.status].label }}
            </StatusPill>
            <ChevronUp v-if="openJob === job.id" class="size-4 text-muted" />
            <ChevronDown v-else class="size-4 text-muted" />
          </button>
          <div v-if="openJob === job.id" class="px-4 pb-4">
            <JobProgress :job="job" />
          </div>
        </li>
      </ul>
    </section>

    <section>
      <div class="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <h2 class="text-lg font-semibold">Clientes</h2>
        <span v-if="brief" class="text-sm text-muted">
          {{ brief.onus.length }} ONU · {{ offline }} sin señal
        </span>
      </div>
      <EmptyState
        v-if="brief && !brief.onus.length"
        :icon="Users"
        title="Esta OLT todavía no tiene ONU autorizadas"
      />
      <template v-else-if="brief">
        <div class="relative mb-3">
          <Search
            class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted"
          />
          <input
            v-model="query"
            class="input pl-9"
            placeholder="Buscar por nombre del cliente o posición (1/12)"
            aria-label="Buscar cliente"
          />
        </div>
        <ul class="card divide-y divide-line">
          <li v-for="row in customers.slice(0, limit)" :key="rowKey(row)">
            <button
              type="button"
              class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-subtle"
              :aria-expanded="openRow === rowKey(row)"
              @click="openRow = openRow === rowKey(row) ? null : rowKey(row)"
            >
              <span class="min-w-0 flex-1">
                <span class="block truncate text-sm font-medium">
                  {{ row.description ?? 'Sin nombre' }}
                </span>
                <span class="text-xs text-muted">PON {{ row.pon }} · posición {{ row.onu }}</span>
              </span>
              <StatusPill :tone="row.up ? 'success' : 'danger'">
                {{ row.up ? 'En línea' : 'Sin señal' }}
              </StatusPill>
              <ChevronUp v-if="openRow === rowKey(row)" class="size-4 text-muted" />
              <ChevronDown v-else class="size-4 text-muted" />
            </button>
            <div v-if="openRow === rowKey(row)" class="px-4 pb-4">
              <OnuActions
                :olt-id="id"
                :onu="row"
                :plans="plans.data.value ?? []"
                @changed="onFinished"
              />
            </div>
          </li>
          <li v-if="!customers.length" class="px-4 py-6 text-sm text-muted">
            Ningún cliente coincide con «{{ query }}».
          </li>
        </ul>
        <button
          v-if="customers.length > limit"
          type="button"
          class="btn-ghost mt-2 text-sm"
          @click="limit += 40"
        >
          Ver más ({{ customers.length - limit }})
        </button>
      </template>
      <div v-else-if="scan.waiting.value" class="card p-6 text-sm text-muted">
        Leyendo los clientes de la OLT…
      </div>
    </section>

    <p class="text-xs text-muted">
      <LayoutTemplate class="mr-1 inline size-3.5" />
      Los planes se manejan en
      <RouterLink :to="{ name: 'templates' }" class="underline">Planes</RouterLink>.
    </p>
  </div>
</template>
