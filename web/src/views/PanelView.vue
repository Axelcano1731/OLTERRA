<script setup lang="ts">
import {
  ArrowRight,
  Bell,
  Cable,
  ChevronRight,
  Circle,
  CircleCheck,
  Database,
  ExternalLink,
  PlugZap,
  Search,
  ShieldCheck,
  TriangleAlert,
  WifiOff,
} from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'

import { getHealth, getMonitoringSummary, listOlts, listRecons, listRouters } from '@/api'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { plural, timeAgo } from '@/lib/format'
import { oltBrand, oltStatus } from '@/lib/labels'
import { useAsync } from '@/lib/useAsync'
import { session } from '@/session'

const router = useRouter()
const health = useAsync(getHealth)
const summary = useAsync(getMonitoringSummary)
const olts = useAsync(listOlts)
const routers = useAsync(listRouters)
const recons = useAsync(() => listRecons(1))

const latest = computed(() => recons.data.value?.[0])
const brands = computed(
  () => new Map((olts.data.value ?? []).map((olt) => [olt.id, oltBrand(olt.driver)] as const)),
)

const search = ref('')
function find(): void {
  const q = search.value.trim()
  void router.push({ name: 'clients', query: q ? { q } : {} })
}

const services = computed(() => {
  const h = health.data.value
  if (!h) return []
  return [
    {
      label: 'Base de datos',
      icon: Database,
      ok: h.database === 'ok',
      text: h.database === 'ok' ? 'Conectada' : 'Sin conexión',
    },
    {
      label: 'Ejecutor (NATS)',
      icon: Cable,
      ok: h.nats === 'conectado',
      text: h.nats === 'conectado' ? 'Conectado' : 'Sin conexión: las consultas a la OLT no salen',
    },
    {
      label: 'Bóveda',
      icon: ShieldCheck,
      ok: h.vault === 'lista',
      text: h.vault === 'lista' ? 'Lista' : 'Sin llave maestra: no se guardan credenciales',
    },
  ]
})

interface Step {
  title: string
  text: string
  done: boolean
  to: RouteLocationRaw
  action: string
}

const steps = computed<Step[]>(() => [
  {
    title: 'Conecta tu MikroTik al túnel',
    text: 'Un script para que Olterra llegue a tus OLT sin abrir puertos.',
    done: (routers.data.value?.length ?? 0) > 0,
    to: { name: 'tunnel' },
    action: 'Ir al túnel',
  },
  {
    title: 'Agrega tu OLT',
    text: 'Con su IP en tu red y sus credenciales, que quedan cifradas.',
    done: (olts.data.value?.length ?? 0) > 0,
    to: { name: 'olt-new' },
    action: 'Agregar OLT',
  },
  {
    title: 'Concilia OLT, MikroTik y CRM',
    text: 'Sube los exports que ya tienes y mira qué no cuadra.',
    done: latest.value !== undefined,
    to: { name: 'recon-new' },
    action: 'Conciliar',
  },
])
const pending = computed(() => steps.value.filter((step) => !step.done).length)
const loaded = computed(() => !!routers.data.value && !!olts.data.value && !!recons.data.value)
</script>

<template>
  <PageHeader
    :title="session.me ? `Hola, ${session.me.tenant.name}` : 'Inicio'"
    description="Así está tu red ahora."
  >
    <template #actions>
      <RouterLink :to="{ name: 'provision' }" class="btn-primary">
        <PlugZap class="size-4" />
        Autorizar ONU
      </RouterLink>
    </template>
  </PageHeader>

  <form class="mb-6" role="search" @submit.prevent="find">
    <label class="relative block">
      <span class="sr-only">Buscar cliente</span>
      <Search
        class="pointer-events-none absolute top-1/2 left-4 size-5 -translate-y-1/2 text-muted"
      />
      <input
        v-model="search"
        type="search"
        class="input py-3 pl-12 text-base"
        placeholder="Buscar un cliente por nombre, serial o posición (1/12) y pulsar Enter"
      />
    </label>
  </form>

  <!-- La red de un vistazo: cada cifra lleva a su lista -->
  <section v-if="summary.data.value" class="grid grid-cols-2 gap-3 lg:grid-cols-4">
    <RouterLink :to="{ name: 'clients' }" class="card p-5 hover:border-accent/50">
      <p class="flex items-center gap-1.5 text-sm text-muted">
        <CircleCheck class="size-4 text-success" /> Clientes en línea
      </p>
      <p class="mt-2 text-3xl font-semibold">
        {{ summary.data.value.online.toLocaleString('es-CO') }}
      </p>
      <p class="text-xs text-muted">de {{ summary.data.value.onus.toLocaleString('es-CO') }}</p>
    </RouterLink>
    <RouterLink
      :to="{ name: 'clients', query: { estado: 'offline' } }"
      class="card p-5 hover:border-accent/50"
    >
      <p class="flex items-center gap-1.5 text-sm text-muted">
        <WifiOff class="size-4 text-danger" /> Caídos
      </p>
      <p class="mt-2 text-3xl font-semibold">
        {{ summary.data.value.offline.toLocaleString('es-CO') }}
      </p>
      <p class="text-xs text-muted">ver cuáles</p>
    </RouterLink>
    <RouterLink
      :to="{ name: 'clients', query: { estado: 'low' } }"
      class="card p-5 hover:border-accent/50"
    >
      <p class="flex items-center gap-1.5 text-sm text-muted">
        <TriangleAlert class="size-4 text-warning" /> Señal baja
      </p>
      <p class="mt-2 text-3xl font-semibold">
        {{ summary.data.value.low_signal.toLocaleString('es-CO') }}
      </p>
      <p class="text-xs text-muted">por debajo de −25 dBm</p>
    </RouterLink>
    <RouterLink :to="{ name: 'alarms' }" class="card p-5 hover:border-accent/50">
      <p class="flex items-center gap-1.5 text-sm text-muted">
        <Bell class="size-4 text-danger" /> Alarmas abiertas
      </p>
      <p class="mt-2 text-3xl font-semibold">
        {{ summary.data.value.critical + summary.data.value.warning }}
      </p>
      <p class="text-xs text-muted">
        {{ plural(summary.data.value.critical, 'crítica', 'críticas') }}
      </p>
    </RouterLink>
  </section>
  <p v-else-if="summary.error.value" class="card p-5 text-sm text-danger">
    {{ summary.error.value }}
  </p>

  <div class="mt-6 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
    <section class="card p-5" aria-labelledby="olts">
      <div class="flex items-center justify-between">
        <h2 id="olts" class="font-semibold">Tus OLT</h2>
        <RouterLink :to="{ name: 'olts' }" class="text-sm text-muted hover:text-ink">
          Administrar
        </RouterLink>
      </div>
      <p v-if="!summary.data.value?.olts.length" class="mt-3 text-sm text-muted">
        Todavía no hay OLT.
        <RouterLink :to="{ name: 'olt-new' }" class="font-medium text-accent">Agrega una</RouterLink
        >.
      </p>
      <ul v-else class="mt-3 divide-y divide-line">
        <li v-for="olt in summary.data.value.olts" :key="olt.id">
          <RouterLink
            :to="{ name: 'clients', query: { olt: olt.id } }"
            class="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-subtle"
          >
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm font-medium">{{ olt.name }}</span>
              <span class="text-xs text-muted">
                {{ brands.get(olt.id) ?? '' }} · {{ olt.online }}/{{ olt.onus }} en línea
                <template v-if="olt.low_signal"> · {{ olt.low_signal }} con señal baja</template>
                <template v-if="olt.monitored_at"> · {{ timeAgo(olt.monitored_at) }}</template>
              </span>
            </span>
            <StatusPill :tone="oltStatus(olt.status).tone">
              {{ oltStatus(olt.status).label }}
            </StatusPill>
            <ChevronRight class="size-4 text-muted" />
          </RouterLink>
        </li>
      </ul>
    </section>

    <div class="space-y-4">
      <section v-if="loaded && pending" class="card p-5" aria-labelledby="pasos">
        <div class="flex items-center justify-between">
          <h2 id="pasos" class="font-semibold">Primeros pasos</h2>
          <StatusPill tone="accent">{{ steps.length - pending }} de {{ steps.length }}</StatusPill>
        </div>
        <ol class="mt-3 space-y-1">
          <li
            v-for="step in steps"
            :key="step.title"
            class="flex items-start gap-3 rounded-lg p-2 hover:bg-subtle"
          >
            <CircleCheck v-if="step.done" class="mt-0.5 size-5 shrink-0 text-success" />
            <Circle v-else class="mt-0.5 size-5 shrink-0 text-muted/60" />
            <div class="min-w-0 flex-1">
              <p :class="['text-sm font-medium', step.done ? 'text-muted line-through' : '']">
                {{ step.title }}
              </p>
              <p class="text-xs text-muted">{{ step.text }}</p>
            </div>
            <RouterLink
              v-if="!step.done"
              :to="step.to"
              class="btn-ghost shrink-0 px-2 py-1 text-xs text-accent"
            >
              {{ step.action }}
              <ArrowRight class="size-3.5" />
            </RouterLink>
          </li>
        </ol>
      </section>

      <section class="card p-5" aria-labelledby="plataforma">
        <h2 id="plataforma" class="font-semibold">Estado de la plataforma</h2>
        <p v-if="health.error.value" class="mt-3 text-sm text-danger">{{ health.error.value }}</p>
        <ul v-else class="mt-3 space-y-3">
          <li v-for="service in services" :key="service.label" class="flex items-start gap-3">
            <component :is="service.icon" class="mt-0.5 size-4 shrink-0 text-muted" />
            <div class="min-w-0 flex-1">
              <p class="text-sm">{{ service.label }}</p>
              <p :class="['text-xs', service.ok ? 'text-success' : 'text-danger']">
                {{ service.text }}
              </p>
            </div>
          </li>
        </ul>
        <div
          class="mt-4 flex items-center justify-between border-t border-line pt-3 text-xs text-muted"
        >
          <span>Versión {{ health.data.value?.version ?? '…' }}</span>
          <a
            href="/docs"
            target="_blank"
            rel="noopener"
            class="inline-flex items-center gap-1 hover:text-ink"
          >
            API
            <ExternalLink class="size-3" />
          </a>
        </div>
      </section>
    </div>
  </div>
</template>
