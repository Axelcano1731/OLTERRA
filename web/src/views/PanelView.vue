<script setup lang="ts">
import {
  ArrowRight,
  Cable,
  Circle,
  CircleCheck,
  Database,
  ExternalLink,
  GitCompareArrows,
  Server,
  ShieldCheck,
  Waypoints,
} from '@lucide/vue'
import { computed } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

import { getHealth, listOlts, listRecons, listRouters } from '@/api'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { timeAgo } from '@/lib/format'
import { useAsync } from '@/lib/useAsync'
import { session } from '@/session'

const health = useAsync(getHealth)
const olts = useAsync(listOlts)
const routers = useAsync(listRouters)
const recons = useAsync(() => listRecons(1))

const latest = computed(() => recons.data.value?.[0])

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
    text: 'Un script de WireGuard para que Olterra llegue a tus OLT sin abrir puertos.',
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

const doneSteps = computed(() => steps.value.filter((step) => step.done).length)
</script>

<template>
  <PageHeader
    :title="session.me ? `Hola, ${session.me.tenant.name}` : 'Panel'"
    description="Lo que hay hoy en Olterra para tu red."
  />

  <div class="grid gap-4 sm:grid-cols-3">
    <RouterLink :to="{ name: 'olts' }" class="card group p-5 hover:border-accent/50">
      <div class="flex items-center justify-between text-sm text-muted">
        OLT
        <Server class="size-4" />
      </div>
      <p class="mt-3 text-3xl font-semibold tabular-nums">
        {{ olts.data.value?.length ?? '—' }}
      </p>
      <p class="mt-1 text-xs text-muted">
        {{ olts.error.value ?? 'dadas de alta' }}
      </p>
    </RouterLink>

    <RouterLink :to="{ name: 'tunnel' }" class="card group p-5 hover:border-accent/50">
      <div class="flex items-center justify-between text-sm text-muted">
        MikroTik en el túnel
        <Waypoints class="size-4" />
      </div>
      <p class="mt-3 text-3xl font-semibold tabular-nums">
        {{ routers.data.value?.length ?? '—' }}
      </p>
      <p class="mt-1 text-xs text-muted">
        {{ routers.error.value ?? 'con script generado' }}
      </p>
    </RouterLink>

    <RouterLink
      :to="latest ? { name: 'recon', params: { id: latest.id } } : { name: 'recons' }"
      class="card group p-5 hover:border-accent/50"
    >
      <div class="flex items-center justify-between text-sm text-muted">
        Última conciliación
        <GitCompareArrows class="size-4" />
      </div>
      <template v-if="latest">
        <div class="mt-3 flex flex-wrap items-baseline gap-2">
          <span class="text-3xl font-semibold text-danger tabular-nums">
            {{ latest.counts.error ?? 0 }}
          </span>
          <span class="text-sm text-muted">errores</span>
          <span class="text-xl font-semibold text-warning tabular-nums">
            {{ latest.counts.advertencia ?? 0 }}
          </span>
          <span class="text-sm text-muted">advertencias</span>
        </div>
        <p class="mt-1 text-xs text-muted">{{ timeAgo(latest.created_at) }}</p>
      </template>
      <template v-else>
        <p class="mt-3 text-3xl font-semibold">—</p>
        <p class="mt-1 text-xs text-muted">
          {{ recons.error.value ?? 'Todavía no has corrido una' }}
        </p>
      </template>
    </RouterLink>
  </div>

  <div class="mt-6 grid gap-4 lg:grid-cols-[1.4fr_1fr]">
    <section class="card p-5" aria-labelledby="pasos">
      <div class="flex items-center justify-between">
        <h2 id="pasos" class="font-semibold">Primeros pasos</h2>
        <StatusPill :tone="doneSteps === steps.length ? 'success' : 'accent'">
          {{ doneSteps }} de {{ steps.length }}
        </StatusPill>
      </div>
      <ol class="mt-4 space-y-1">
        <li
          v-for="step in steps"
          :key="step.title"
          class="flex items-start gap-3 rounded-lg p-3 hover:bg-subtle"
        >
          <CircleCheck v-if="step.done" class="mt-0.5 size-5 shrink-0 text-success" />
          <Circle v-else class="mt-0.5 size-5 shrink-0 text-muted/60" />
          <div class="min-w-0 flex-1">
            <p :class="['text-sm font-medium', step.done ? 'text-muted line-through' : 'text-ink']">
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
      <p v-if="health.error.value" class="mt-4 text-sm text-danger">
        {{ health.error.value }}
      </p>
      <ul v-else class="mt-4 space-y-3">
        <li v-for="service in services" :key="service.label" class="flex items-start gap-3">
          <component :is="service.icon" class="mt-0.5 size-4 shrink-0 text-muted" />
          <div class="min-w-0 flex-1">
            <p class="text-sm">{{ service.label }}</p>
            <p :class="['text-xs', service.ok ? 'text-success' : 'text-danger']">
              {{ service.text }}
            </p>
          </div>
          <span
            :class="[
              'mt-1.5 size-2 shrink-0 rounded-full',
              service.ok ? 'bg-success' : 'bg-danger',
            ]"
            aria-hidden="true"
          />
        </li>
      </ul>
      <div
        class="mt-5 flex items-center justify-between border-t border-line pt-4 text-xs text-muted"
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
</template>
