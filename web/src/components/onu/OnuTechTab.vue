<script setup lang="ts">
import { LoaderCircle, RefreshCw } from '@lucide/vue'
import { computed, onMounted } from 'vue'

import type { OnuStatus } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import { formatDbm } from '@/lib/monitoring'
import { useOnuRead } from '@/lib/useOnuRead'

const props = defineProps<{ status: OnuStatus; driver: string }>()

// Lo que cada marca sabe decir de una sola ONU (todas son lecturas).
const READS: Record<string, { key: string; title: string }[]> = {
  'vsol-gpon': [
    { key: 'onu.optical', title: 'Óptica' },
    { key: 'onu.distance', title: 'Distancia' },
    { key: 'onu.capability', title: 'Puertos' },
  ],
  'hsgq-gpon': [
    { key: 'onu.detail', title: 'Detalle' },
    { key: 'onu.version', title: 'Equipo' },
  ],
  'bcd-gpon': [
    { key: 'onu.detail', title: 'Detalle' },
    { key: 'onu.version', title: 'Equipo' },
    { key: 'onu.optical', title: 'Óptica' },
  ],
}
const reads = computed(() => READS[props.driver] ?? [])

const reader = useOnuRead()
function load(): void {
  if (!reads.value.length) return
  void reader.run(
    props.status.olt_id,
    props.status.pon,
    props.status.onu,
    reads.value.map((read) => read.key),
  )
}
onMounted(load)

const LABELS: Record<string, string> = {
  rx_dbm: 'Señal que recibe la ONU',
  tx_dbm: 'Potencia que transmite',
  olt_rx_dbm: 'Señal que recibe la OLT de ella',
  temperature_c: 'Temperatura',
  voltage_v: 'Voltaje',
  bias_ma: 'Corriente del láser',
  meters: 'Distancia a la OLT',
  distance_m: 'Distancia a la OLT',
  ethernet_ports: 'Puertos Ethernet',
  wifi_ports: 'Wi‑Fi (SSID)',
  onu_type: 'Tipo',
  serial: 'Serial',
  name: 'Nombre en la OLT',
  run_state: 'Estado en la OLT',
  line_profile: 'Perfil de línea',
  service_profile: 'Perfil de servicio',
  last_up: 'Última conexión',
  last_down: 'Última caída',
  last_down_cause: 'Causa de la última caída',
  uptime: 'Tiempo en línea',
  vendor: 'Fabricante',
  model: 'Modelo',
  hardware: 'Hardware',
  software: 'Firmware',
}
const SKIP = new Set(['pon', 'onu', 'phase', 'online'])

const UNITS: Record<string, string> = { temperature_c: '°C', voltage_v: 'V', bias_ma: 'mA' }

function show(key: string, value: unknown): string {
  if (typeof value === 'number') {
    if (key.endsWith('_dbm')) return formatDbm(value)
    if (key === 'meters' || key === 'distance_m') return `${value.toLocaleString('es-CO')} m`
    const unit = UNITS[key]
    return unit ? `${value.toLocaleString('es-CO')} ${unit}` : value.toLocaleString('es-CO')
  }
  if (typeof value === 'string' && value) return value
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  return '—'
}

function readError(key: string): string | null {
  const error = reader.read(key).error
  if (error && /not online|no est[aá] en l[ií]nea/i.test(error)) {
    return 'La ONU no está en línea: la OLT no puede leerla.'
  }
  return error
}

function rows(data: unknown): { key: string; label: string; value: string }[] {
  if (!data || typeof data !== 'object') return []
  return Object.entries(data as Record<string, unknown>)
    .filter(([key]) => !SKIP.has(key))
    .map(([key, value]) => ({ key, label: LABELS[key] ?? key, value: show(key, value) }))
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-sm text-muted">Lo que dice la OLT de esta ONU ahora mismo.</p>
      <button
        type="button"
        class="btn-ghost px-2.5 py-1 text-xs"
        :disabled="reader.waiting.value"
        @click="load"
      >
        <RefreshCw :class="['size-3.5', reader.waiting.value ? 'animate-spin' : '']" />
        Consultar de nuevo
      </button>
    </div>
    <AlertBox v-if="reader.error.value" tone="danger">{{ reader.error.value }}</AlertBox>
    <p v-else-if="reader.waiting.value" class="card p-5 text-sm text-muted">
      <LoaderCircle class="mr-1 inline size-4 animate-spin" />
      Preguntando a la OLT{{ reader.slow.value ? ' (está tardando)' : '' }}…
    </p>
    <div v-else-if="reader.done.value" class="grid gap-4 md:grid-cols-2">
      <section v-for="read in reads" :key="read.key" class="card p-5">
        <h3 class="mb-3 font-semibold">{{ read.title }}</h3>
        <p v-if="readError(read.key)" class="text-sm text-muted">{{ readError(read.key) }}</p>
        <dl v-else class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <template v-for="row in rows(reader.read(read.key).data)" :key="row.key">
            <dt class="text-muted">{{ row.label }}</dt>
            <dd class="tabular-nums">{{ row.value }}</dd>
          </template>
        </dl>
      </section>
    </div>
  </div>
</template>
