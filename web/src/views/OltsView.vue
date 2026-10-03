<script setup lang="ts">
import { Pencil, Plus, Server } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { listOlts, type Olt } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import DeleteOlt from '@/components/DeleteOlt.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { oltStatus } from '@/lib/labels'
import { useAsync } from '@/lib/useAsync'
import { can } from '@/session'

const route = useRoute()
const { data: olts, error, loading, reload } = useAsync(listOlts)

// Qué se borró (aquí o desde el detalle) y si hay que rotar su MikroTik.
const deleted = ref<{ name: string; viaRouter: boolean } | null>(
  typeof route.query.borrada === 'string'
    ? { name: route.query.borrada, viaRouter: route.query.router === '1' }
    : null,
)
const deleteError = ref<string | null>(null)
const canWrite = computed(() => can('olt:write'))

async function onDeleted(olt: Olt): Promise<void> {
  deleteError.value = null
  deleted.value = { name: olt.name, viaRouter: olt.router_id !== null }
  await reload()
}
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

  <AlertBox v-if="deleted" tone="success" :title="`${deleted.name} eliminada`" class="mb-4">
    Se borraron su credencial y su historial de consultas.
    <template v-if="deleted.viaRouter">
      Estaba detrás de un MikroTik: rota sus llaves en
      <RouterLink :to="{ name: 'tunnel' }" class="font-medium underline">Túnel</RouterLink>
      para que su script deje de publicarla.
    </template>
  </AlertBox>
  <AlertBox v-if="deleteError" tone="danger" class="mb-4">{{ deleteError }}</AlertBox>
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
          <th class="px-4 py-2.5"><span class="sr-only">Acciones</span></th>
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
          <td class="px-4 py-3 text-right whitespace-nowrap">
            <template v-if="canWrite">
              <RouterLink
                :to="{ name: 'olt-edit', params: { id: olt.id } }"
                class="btn-ghost px-2 py-1 text-xs"
                :aria-label="`Editar ${olt.name}`"
              >
                <Pencil class="size-3.5" />
                Editar
              </RouterLink>
              <DeleteOlt compact :olt="olt" @deleted="onDeleted" @failed="deleteError = $event" />
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
