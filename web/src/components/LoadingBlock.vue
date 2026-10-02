<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue'

defineProps<{ text?: string }>()
</script>

<template>
  <div class="flex items-center justify-center gap-2 py-12 text-sm text-muted" role="status">
    <LoaderCircle class="size-4 animate-spin" />
    {{ text ?? 'Cargando…' }}
  </div>
</template>
