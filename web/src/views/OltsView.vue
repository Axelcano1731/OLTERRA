<script setup lang="ts">
import { Plus, Server } from '@lucide/vue'

import { listOlts } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { oltStatus } from '@/lib/labels'
import { useAsync } from '@/lib/useAsync'
import { can } from '@/session'

const { data: olts, error, loading } = useAsync(listOlts)
</script>

<template>
  <PageHeader title="OLT" description="Las OLT VSOL de tu red y cómo llega Olterra a cada una.">
    <template #actions>
      <RouterLink v-if="can('olt:write')" :to="{ name: 'olt-new' }" class="btn-primary">
        <Plus class="size-4" />
        Agregar OLT
      </RouterLink>
    </template>
  </PageHeader>

  <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>
  <LoadingBlock v-else-if="loading && !olts" />
  <EmptyState
    v-else-if="olts && olts.length === 0"
    :icon="Server"
    title="Todavía no hay OLT"
    text="Agrega tu primera OLT con su IP y sus credenciales. Si está detrás de tu MikroTik, conecta primero el túnel."
  >
    <RouterLink v-if="can('olt:write')" :to="{ name: 'olt-new' }" class="btn-primary">
      <Plus class="size-4" />
      Agregar OLT
    </RouterLink>
    <RouterLink :to="{ name: 'tunnel' }" class="btn-secondary">Ver el túnel</RouterLink>
  </EmptyState>

  <div v-else-if="olts" class="card overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="table-head">
        <tr>
          <th class="px-4 py-2.5">Nombre</th>
          <th class="px-4 py-2.5">Modelo</th>
          <th class="px-4 py-2.5">IP en tu red</th>
          <th class="px-4 py-2.5">IP en el túnel</th>
          <th class="px-4 py-2.5">Estado</th>
          <th class="px-4 py-2.5">Alta</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="olt in olts" :key="olt.id" class="hover:bg-subtle/60">
          <td class="px-4 py-3 font-medium">
            <RouterLink :to="{ name: 'olt', params: { id: olt.id } }" class="hover:text-accent">
              {{ olt.name }}
            </RouterLink>
          </td>
          <td class="px-4 py-3 text-muted">
            {{ olt.model ?? 'Sin modelo' }}
            <span v-if="olt.firmware" class="text-xs">· {{ olt.firmware }}</span>
          </td>
          <td class="px-4 py-3 font-mono text-xs">{{ olt.real_ip ?? '—' }}</td>
          <td class="px-4 py-3 font-mono text-xs">{{ olt.nat_ip ?? 'Directa' }}</td>
          <td class="px-4 py-3">
            <StatusPill :tone="oltStatus(olt.status).tone">{{
              oltStatus(olt.status).label
            }}</StatusPill>
          </td>
          <td class="px-4 py-3 text-muted" :title="formatDateTime(olt.created_at)">
            {{ timeAgo(olt.created_at) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
