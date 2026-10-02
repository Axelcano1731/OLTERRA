<script setup lang="ts">
import type { Component } from 'vue'

defineProps<{ icon: Component; title: string; text?: string }>()
</script>

<template>
  <div class="card flex flex-col items-center px-6 py-12 text-center">
    <span class="mb-4 grid size-12 place-items-center rounded-full bg-accent-soft text-accent">
      <component :is="icon" class="size-6" />
    </span>
    <h2 class="text-base font-semibold text-ink">{{ title }}</h2>
    <p v-if="text" class="mt-1 max-w-md text-sm text-muted">{{ text }}</p>
    <div v-if="$slots.default" class="mt-5 flex flex-wrap justify-center gap-2">
      <slot />
    </div>
  </div>
</template>
