<script setup lang="ts">
// El corte de una fibra: núcleo y revestimiento.
defineProps<{ compact?: boolean }>()
</script>

<template>
  <span class="inline-flex items-center gap-2.5">
    <svg viewBox="0 0 32 32" class="size-8 shrink-0" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#1a2744" />
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="#8b93ff" stroke-width="3" />
      <circle cx="16" cy="16" r="2.6" fill="#f5f7fb" />
    </svg>
    <span v-if="!compact" class="text-lg font-semibold tracking-tight">Olterra</span>
  </span>
</template>
