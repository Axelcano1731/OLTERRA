<script setup lang="ts">
import { GitCompareArrows, LoaderCircle, Plus, Sparkles } from '@lucide/vue'
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { listRecons, runReconDemo } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { actorLabel, RECON_SOURCE } from '@/lib/labels'
import { errorText, useAsync } from '@/lib/useAsync'
import { can } from '@/session'

const router = useRouter()
const recons = useAsync(() => listRecons(50))

const demoBusy = ref(false)
const demoError = ref<string | null>(null)

async function demo(): Promise<void> {
  demoBusy.value = true
  demoError.value = null
  try {
    const run = await runReconDemo()
    await router.push({ name: 'recon', params: { id: run.id } })
  } catch (caught) {
    demoError.value = errorText(caught)
  } finally {
    demoBusy.value = false
  }
}
</script>

<template>
  <PageHeader
    title="Conciliación"
    description="Cruza la OLT, el MikroTik y el CRM: ONUs sin cliente, seriales cruzados, clientes cortados que siguen navegando, usuarios PPPoE mal escritos…"
  >
    <template #actions>
      <template v-if="can('recon:write')">
        <button type="button" class="btn-secondary" :disabled="demoBusy" @click="demo">
          <LoaderCircle v-if="demoBusy" class="size-4 animate-spin" />
          <Sparkles v-else class="size-4" />
          Probar con la demo
        </button>
        <RouterLink :to="{ name: 'recon-new' }" class="btn-primary">
          <Plus class="size-4" />
          Nueva conciliación
        </RouterLink>
      </template>
    </template>
  </PageHeader>

  <AlertBox v-if="demoError" tone="danger" class="mb-4">{{ demoError }}</AlertBox>
  <AlertBox v-if="recons.error.value" tone="danger">{{ recons.error.value }}</AlertBox>
  <LoadingBlock v-else-if="recons.loading.value && !recons.data.value" />

  <EmptyState
    v-else-if="recons.data.value && recons.data.value.length === 0"
    :icon="GitCompareArrows"
    title="Todavía no has conciliado"
    text="Sube los exports que ya tienes (ONUs, secretos PPPoE, sesiones y clientes del CRM) o mira primero la demo con datos sintéticos."
  >
    <template v-if="can('recon:write')">
      <RouterLink :to="{ name: 'recon-new' }" class="btn-primary">
        <Plus class="size-4" />
        Subir archivos
      </RouterLink>
      <button type="button" class="btn-secondary" :disabled="demoBusy" @click="demo">
        <Sparkles class="size-4" />
        Probar con la demo
      </button>
    </template>
  </EmptyState>

  <div v-else-if="recons.data.value" class="card overflow-x-auto">
    <table class="w-full text-sm">
      <thead class="table-head">
        <tr>
          <th class="px-4 py-2.5">Fecha</th>
          <th class="px-4 py-2.5">Origen</th>
          <th class="px-4 py-2.5">Datos</th>
          <th class="px-4 py-2.5">Hallazgos</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr
          v-for="run in recons.data.value"
          :key="run.id"
          class="cursor-pointer hover:bg-subtle/60"
          @click="router.push({ name: 'recon', params: { id: run.id } })"
        >
          <td class="px-4 py-3">
            <RouterLink
              :to="{ name: 'recon', params: { id: run.id } }"
              class="font-medium hover:text-accent"
              @click.stop
            >
              {{ formatDateTime(run.created_at) }}
            </RouterLink>
            <p class="text-xs text-muted">
              {{ timeAgo(run.created_at) }} · {{ actorLabel(run.requested_by) }}
            </p>
          </td>
          <td class="px-4 py-3">
            <StatusPill :tone="run.source === 'demo' ? 'accent' : 'neutral'">
              {{ RECON_SOURCE[run.source] }}
            </StatusPill>
            <p v-if="run.files.length" class="mt-1 max-w-[16rem] truncate text-xs text-muted">
              {{ run.files.map((file) => file.name).join(', ') }}
            </p>
          </td>
          <td class="px-4 py-3 text-xs text-muted">
            {{ run.counts.onus ?? 0 }} ONUs · {{ run.counts.secretos ?? 0 }} secretos ·
            {{ run.counts.clientes ?? 0 }} clientes
          </td>
          <td class="px-4 py-3">
            <div class="flex flex-wrap gap-1">
              <StatusPill tone="danger">{{ run.counts.error ?? 0 }} errores</StatusPill>
              <StatusPill tone="warning">{{ run.counts.advertencia ?? 0 }} advertencias</StatusPill>
              <StatusPill tone="info">{{ run.counts.info ?? 0 }} por documentar</StatusPill>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
