<script setup lang="ts">
import { ArrowLeft } from '@lucide/vue'
import type { RouteLocationRaw } from 'vue-router'

defineProps<{
  title: string
  description?: string
  back?: RouteLocationRaw
  backLabel?: string
}>()
</script>

<template>
  <header class="mb-6 flex flex-wrap items-end justify-between gap-4">
    <div class="min-w-0">
      <RouterLink
        v-if="back"
        :to="back"
        class="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink"
      >
        <ArrowLeft class="size-4" />
        {{ backLabel ?? 'Volver' }}
      </RouterLink>
      <h1 class="text-2xl font-semibold tracking-tight text-ink">{{ title }}</h1>
      <p v-if="description" class="mt-1 max-w-2xl text-sm text-muted">{{ description }}</p>
      <slot name="meta" />
    </div>
    <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
