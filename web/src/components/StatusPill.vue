<script setup lang="ts">
import type { Tone } from '@/lib/labels'

withDefaults(defineProps<{ tone?: Tone }>(), { tone: 'neutral' })

const TONES: Record<Tone, string> = {
  neutral: 'bg-subtle text-muted',
  accent: 'bg-accent-soft text-accent',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
  info: 'bg-info-soft text-info',
}
</script>

<template>
  <span
    :class="[
      'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap',
      TONES[tone],
    ]"
  >
    <slot />
  </span>
</template>
