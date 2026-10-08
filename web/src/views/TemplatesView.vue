<script setup lang="ts">
import { LayoutTemplate, Pencil, Plus, Trash } from '@lucide/vue'
import { ref } from 'vue'

import { deleteTemplate, listTemplates, type ProvisionTemplate } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { can } from '@/session'
import { errorText, useAsync } from '@/lib/useAsync'

const templates = useAsync(listTemplates)
const confirming = ref<string | null>(null)
const error = ref<string | null>(null)

function summary(template: ProvisionTemplate): string {
  const vlans = [...new Set(template.body.services.map((service) => service.vlan))]
  return vlans.length ? `VLAN ${vlans.join(', ')}` : 'Sin servicio'
}

async function remove(template: ProvisionTemplate): Promise<void> {
  error.value = null
  try {
    await deleteTemplate(template.id)
    await templates.reload()
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    confirming.value = null
  }
}
</script>

<template>
  <PageHeader
    title="Planes"
    description="Cómo se configura cada plan de servicio en la ONU (VLAN, velocidad, PPPoE, WiFi). Se copian de un cliente que ya navega o se llenan a mano."
  >
    <template v-if="can('olt:write')" #actions>
      <RouterLink :to="{ name: 'template-new' }" class="btn-primary">
        <Plus class="size-4" />
        Nuevo plan
      </RouterLink>
    </template>
  </PageHeader>

  <AlertBox v-if="error" tone="danger" class="mb-6">{{ error }}</AlertBox>
  <AlertBox v-if="templates.error.value" tone="danger">{{ templates.error.value }}</AlertBox>
  <LoadingBlock v-else-if="templates.loading.value && !templates.data.value" />
  <EmptyState
    v-else-if="!templates.data.value?.length"
    :icon="LayoutTemplate"
    title="Todavía no hay planes"
    text="La forma más rápida: en Aprovisionar, abre un cliente que ya navega y pulsa «Copiar como plan»."
  />
  <div v-else class="card divide-y divide-line">
    <div
      v-for="template in templates.data.value"
      :key="template.id"
      class="flex flex-wrap items-center justify-between gap-3 px-5 py-4"
    >
      <div class="min-w-0">
        <p class="font-medium">{{ template.name }}</p>
        <div class="mt-1 flex flex-wrap gap-2 text-sm text-muted">
          <span>{{ summary(template) }}</span>
          <StatusPill :tone="template.body.wan ? 'accent' : 'neutral'">
            {{ template.body.wan ? 'Router PPPoE' : 'Bridge' }}
          </StatusPill>
          <StatusPill v-if="template.body.wifi" tone="accent">WiFi</StatusPill>
          <StatusPill v-if="template.body.management" tone="accent">Gestión remota</StatusPill>
        </div>
      </div>
      <div v-if="can('olt:write')" class="flex items-center gap-1">
        <template v-if="confirming === template.id">
          <span class="text-xs text-danger">¿Eliminarlo?</span>
          <button
            type="button"
            class="btn px-2.5 py-1 text-xs bg-danger text-on-accent hover:opacity-90"
            @click="remove(template)"
          >
            Eliminar
          </button>
          <button type="button" class="btn-ghost px-2 py-1 text-xs" @click="confirming = null">
            No
          </button>
        </template>
        <template v-else>
          <RouterLink
            :to="{ name: 'template-edit', params: { id: template.id } }"
            class="btn-ghost px-2 py-1 text-xs"
          >
            <Pencil class="size-3.5" />
            Editar
          </RouterLink>
          <button
            type="button"
            class="btn-ghost px-2 py-1 text-xs text-danger"
            @click="confirming = template.id"
          >
            <Trash class="size-3.5" />
            Eliminar
          </button>
        </template>
      </div>
    </div>
  </div>
</template>
