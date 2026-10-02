<script setup lang="ts">
import { Compass } from '@lucide/vue'

import EmptyState from '@/components/EmptyState.vue'
</script>

<template>
  <div class="grid min-h-dvh place-items-center p-6">
    <EmptyState
      :icon="Compass"
      title="Esta página no existe"
      text="Puede que el enlace esté viejo."
    >
      <RouterLink :to="{ name: 'panel' }" class="btn-primary">Ir al panel</RouterLink>
    </EmptyState>
  </div>
</template>
