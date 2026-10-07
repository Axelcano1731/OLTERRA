<script setup lang="ts">
import { computed } from 'vue'

import type { Plan, PlanOutput } from '@/api'
import { formatDateTime } from '@/lib/format'
import { planStatus } from '@/lib/labels'

import AlertBox from './AlertBox.vue'
import DataView from './DataView.vue'
import StatusPill from './StatusPill.vue'

const props = defineProps<{ plan: Plan }>()

const outputs = computed(() => props.plan.result?.outputs ?? [])
const status = computed(() => planStatus(props.plan.status))

const seconds = computed(() => {
  if (!props.plan.finished_at) return null
  const ms = new Date(props.plan.finished_at).getTime() - new Date(props.plan.created_at).getTime()
  return Math.max(0, Math.round(ms / 100) / 10)
})

function target(output: PlanOutput): string {
  const parts = []
  if (output.params.pon !== undefined) parts.push(`PON ${output.params.pon}`)
  if (output.params.onu !== undefined) parts.push(`ONU ${output.params.onu}`)
  return parts.join(' · ')
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
      <StatusPill :tone="status.tone">{{ status.label }}</StatusPill>
      <span>{{ formatDateTime(plan.created_at) }}</span>
      <span v-if="seconds !== null">· {{ seconds.toLocaleString('es-CO') }} s</span>
      <span v-if="plan.result?.executor">· ejecutor {{ plan.result.executor }}</span>
    </div>

    <AlertBox v-if="plan.result?.error" tone="danger" title="El plan no se completó">
      {{ plan.result.error }}
    </AlertBox>

    <article
      v-for="(output, index) in outputs"
      :key="`${output.key}-${index}`"
      class="overflow-hidden rounded-lg border border-line"
    >
      <header
        class="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-subtle px-4 py-2"
      >
        <div class="flex min-w-0 items-center gap-2">
          <span class="truncate font-mono text-sm font-medium">{{ output.key }}</span>
          <span v-if="target(output)" class="text-xs text-muted">{{ target(output) }}</span>
        </div>
        <StatusPill :tone="output.ok ? 'success' : 'danger'">
          {{ output.ok ? 'OK' : 'Falló' }}
        </StatusPill>
      </header>
      <div
        v-if="output.error || output.parse_error || output.data !== undefined || output.output"
        class="space-y-3 p-4"
      >
        <p v-if="output.error" class="text-sm text-danger">{{ output.error }}</p>
        <AlertBox v-if="output.parse_error" tone="warning" title="Salida no reconocida">
          {{ output.parse_error }}. Abajo va el texto tal cual.
        </AlertBox>
        <DataView v-if="output.data !== undefined" :data="output.data" />
        <details v-if="output.output" :open="output.data === undefined">
          <summary class="cursor-pointer text-xs font-medium text-muted hover:text-ink">
            Salida de la OLT
          </summary>
          <pre
            class="mt-2 max-h-96 overflow-auto rounded-md bg-subtle p-3 font-mono text-xs leading-relaxed"
            >{{ output.output }}</pre>
        </details>
      </div>
    </article>
  </div>
</template>
