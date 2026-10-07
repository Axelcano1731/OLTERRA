<script setup lang="ts">
import { Check, Circle, LoaderCircle, Minus, X } from '@lucide/vue'

import type { JobStepStatus, ProvisionJob } from '@/api'

import AlertBox from './AlertBox.vue'

defineProps<{ job: ProvisionJob }>()

const ICONS: Record<JobStepStatus, { icon: unknown; tone: string }> = {
  done: { icon: Check, tone: 'bg-success-soft text-success' },
  running: { icon: LoaderCircle, tone: 'bg-accent-soft text-accent' },
  failed: { icon: X, tone: 'bg-danger-soft text-danger' },
  pending: { icon: Circle, tone: 'bg-subtle text-muted' },
  skipped: { icon: Minus, tone: 'bg-subtle text-muted' },
}
</script>

<template>
  <div class="space-y-3">
    <ol class="space-y-2">
      <li v-for="step in job.steps" :key="step.key" class="flex items-start gap-3">
        <span
          :class="[
            'mt-0.5 grid size-6 shrink-0 place-items-center rounded-full',
            ICONS[step.status].tone,
          ]"
        >
          <component
            :is="ICONS[step.status].icon"
            :class="['size-3.5', step.status === 'running' ? 'animate-spin' : '']"
          />
        </span>
        <div class="min-w-0">
          <p
            :class="[
              'text-sm',
              step.status === 'pending' || step.status === 'skipped' ? 'text-muted' : 'text-ink',
            ]"
          >
            {{ step.label }}
          </p>
          <p v-if="step.message" class="text-xs text-muted">{{ step.message }}</p>
        </div>
      </li>
    </ol>

    <AlertBox v-if="job.status === 'done'" tone="success" title="Listo">
      <template v-if="job.kind === 'authorize'">
        La ONU de <b>{{ job.description }}</b> quedó en el PON {{ job.pon }}, posición
        {{ job.onu }}.
      </template>
      <template v-else>Internet y WiFi configurados.</template>
      <template v-if="job.rx_dbm !== null"> Señal: {{ job.rx_dbm }} dBm.</template>
      <template v-if="job.wifi_ssid"> WiFi: {{ job.wifi_ssid }}.</template>
    </AlertBox>
    <AlertBox v-else-if="job.status === 'failed'" tone="danger" title="No se pudo terminar">
      {{ job.error }}
    </AlertBox>
    <p v-if="job.unverified.length" class="text-xs text-warning">
      Modo laboratorio: corrieron comandos sin validar ({{ job.unverified.join(', ') }}).
    </p>
  </div>
</template>
