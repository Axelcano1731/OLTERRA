<script setup lang="ts">
import { Server } from '@lucide/vue'
import { watch } from 'vue'
import { useRouter } from 'vue-router'

import { listOlts } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import { useAsync } from '@/lib/useAsync'

// "Aprovisionar" del menú: con una sola OLT entra directo; con varias, se elige.
const router = useRouter()
const olts = useAsync(listOlts)

watch(
  () => olts.data.value,
  (list) => {
    const [only] = list ?? []
    if (list?.length === 1 && only) {
      void router.replace({ name: 'olt-provision', params: { id: only.id } })
    }
  },
)
</script>

<template>
  <PageHeader title="Aprovisionar" description="Elige la OLT donde conectaste la ONU." />
  <AlertBox v-if="olts.error.value" tone="danger">{{ olts.error.value }}</AlertBox>
  <LoadingBlock v-else-if="olts.loading.value || olts.data.value?.length === 1" />
  <EmptyState
    v-else-if="!olts.data.value?.length"
    :icon="Server"
    title="Todavía no hay OLT"
    text="Agrega tu OLT y conéctala por el túnel: después aquí aparecen las ONU nuevas."
  />
  <ul v-else class="grid gap-3 sm:grid-cols-2">
    <li v-for="olt in olts.data.value" :key="olt.id">
      <RouterLink
        :to="{ name: 'olt-provision', params: { id: olt.id } }"
        class="card flex items-center gap-3 p-5 hover:border-accent"
      >
        <Server class="size-5 text-accent" />
        <span>
          <span class="block font-medium">{{ olt.name }}</span>
          <span class="text-xs text-muted">{{ olt.model ?? 'Modelo sin indicar' }}</span>
        </span>
      </RouterLink>
    </li>
  </ul>
</template>
