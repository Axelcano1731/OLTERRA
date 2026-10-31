<script setup lang="ts">
import { BellOff, ChevronRight, CircleAlert, TriangleAlert } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { RouteLocationRaw } from 'vue-router'

import { listAlarms, listOlts, type Alarm } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { ALARM_KIND, alarmWhere, duration } from '@/lib/monitoring'
import { useAsync } from '@/lib/useAsync'

const state = ref<'open' | 'all'>('open')
const oltFilter = ref('')
const olts = useAsync(listOlts)
const alarms = useAsync(() => listAlarms(state.value, oltFilter.value || undefined))
watch([state, oltFilter], () => void alarms.reload())

let timer: number | undefined
onMounted(() => (timer = window.setInterval(() => void alarms.reload(), 60_000)))
onBeforeUnmount(() => window.clearInterval(timer))

/** Una alarma de una ONU lleva a su ficha; la de un PON o la OLT, a sus clientes. */
function target(alarm: Alarm): RouteLocationRaw {
  if (alarm.onu > 0) {
    return {
      name: 'onu',
      params: { olt: alarm.olt_id, pon: String(alarm.pon), onu: String(alarm.onu) },
      query: { pestana: 'alarmas' },
    }
  }
  return {
    name: 'clients',
    query: { olt: alarm.olt_id, estado: alarm.kind === 'pon_down' ? 'offline' : undefined },
  }
}

const counts = computed(() => {
  const rows = (alarms.data.value ?? []).filter((alarm) => !alarm.cleared_at)
  return {
    critical: rows.filter((alarm) => alarm.severity === 'critical').length,
    warning: rows.filter((alarm) => alarm.severity === 'warning').length,
  }
})
</script>

<template>
  <PageHeader
    title="Alarmas"
    description="Olterra abre una alarma cuando una OLT no responde, se cae un PON o un cliente, o la señal se sale de rango, y la cierra sola cuando se arregla."
  >
    <template #actions>
      <select v-if="(olts.data.value?.length ?? 0) > 1" v-model="oltFilter" class="input w-auto">
        <option value="">Todas las OLT</option>
        <option v-for="olt in olts.data.value" :key="olt.id" :value="olt.id">{{ olt.name }}</option>
      </select>
      <div class="flex rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
        <button
          type="button"
          :aria-pressed="state === 'open'"
          :class="[
            'rounded-md px-3 py-1',
            state === 'open' ? 'bg-subtle font-medium' : 'text-muted',
          ]"
          @click="state = 'open'"
        >
          Abiertas
        </button>
        <button
          type="button"
          :aria-pressed="state === 'all'"
          :class="[
            'rounded-md px-3 py-1',
            state === 'all' ? 'bg-subtle font-medium' : 'text-muted',
          ]"
          @click="state = 'all'"
        >
          Historial
        </button>
      </div>
    </template>
  </PageHeader>

  <AlertBox v-if="alarms.error.value" tone="danger">{{ alarms.error.value }}</AlertBox>
  <LoadingBlock v-else-if="alarms.loading.value && !alarms.data.value" />
  <EmptyState
    v-else-if="!alarms.data.value?.length"
    :icon="BellOff"
    :title="state === 'open' ? 'Sin alarmas abiertas' : 'Sin alarmas'"
    text="Todo responde y todas las señales están en rango."
  />
  <template v-else>
    <p v-if="state === 'open'" class="mb-3 text-sm text-muted">
      {{ counts.critical }} {{ counts.critical === 1 ? 'crítica' : 'críticas' }} y
      {{ counts.warning }} {{ counts.warning === 1 ? 'aviso' : 'avisos' }}. Las críticas van
      primero.
    </p>
    <ul class="card divide-y divide-line">
      <li v-for="alarm in alarms.data.value" :key="alarm.id">
        <RouterLink :to="target(alarm)" class="flex items-start gap-3 px-5 py-3 hover:bg-subtle">
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
              <template v-if="alarm.cleared_at">
                · duró {{ duration(alarm.started_at, Date.parse(alarm.cleared_at)) }}
              </template>
              <template v-else> · lleva {{ duration(alarm.started_at) }}</template>
            </span>
            <span class="sr-only">Empezó {{ formatDateTime(alarm.started_at) }}</span>
          </span>
          <StatusPill
            :tone="
              alarm.cleared_at ? 'neutral' : alarm.severity === 'critical' ? 'danger' : 'warning'
            "
          >
            {{ alarm.cleared_at ? 'Cerrada' : alarm.severity === 'critical' ? 'Crítica' : 'Aviso' }}
          </StatusPill>
          <ChevronRight class="mt-0.5 size-4 shrink-0 text-muted" />
        </RouterLink>
      </li>
    </ul>
  </template>
</template>
