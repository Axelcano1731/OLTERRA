<script setup lang="ts">
import { LoaderCircle, RefreshCw } from '@lucide/vue'
import { computed, onMounted } from 'vue'

import type { OnuStatus } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import { useOnuRead } from '@/lib/useOnuRead'

const props = defineProps<{ status: OnuStatus }>()

interface MacRow {
  mac: string
  vlan: number | null
  onu: number | null
}

const reader = useOnuRead()
function load(): void {
  void reader.run(props.status.olt_id, props.status.pon, props.status.onu, ['onu.mac'])
}
onMounted(load)

const macs = computed(() => reader.read<MacRow[]>('onu.mac'))
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h3 class="font-semibold">MAC de los equipos</h3>
        <p class="text-sm text-muted">
          Las direcciones que la OLT aprendió detrás de esta ONU (la de su WAN y, si está en modo
          puente, las de los equipos del cliente).
        </p>
      </div>
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
    <AlertBox v-else-if="macs.error" tone="danger">{{ macs.error }}</AlertBox>
    <p v-else-if="reader.done.value && !macs.data?.length" class="card p-5 text-sm text-muted">
      La OLT no tiene MAC aprendidas de esta ONU. Si es un router (HGU), en algunas marcas su MAC
      solo se ve en la sesión PPPoE del MikroTik.
    </p>
    <div v-else-if="macs.data" class="card overflow-x-auto">
      <table class="w-full text-left text-sm">
        <thead class="bg-subtle text-xs text-muted">
          <tr>
            <th class="px-4 py-2 font-medium">MAC</th>
            <th class="px-4 py-2 font-medium">VLAN</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr v-for="row in macs.data" :key="row.mac">
            <td class="px-4 py-2 font-mono">{{ row.mac }}</td>
            <td class="px-4 py-2 tabular-nums">{{ row.vlan ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
