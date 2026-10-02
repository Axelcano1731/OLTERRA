<script setup lang="ts">
import { CircleCheck, CircleX, Info, TriangleAlert } from '@lucide/vue'
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ tone?: 'info' | 'warning' | 'danger' | 'success'; title?: string }>(),
  { tone: 'info', title: undefined },
)

const STYLES = {
  info: { box: 'bg-info-soft', text: 'text-info', icon: Info },
  warning: { box: 'bg-warning-soft', text: 'text-warning', icon: TriangleAlert },
  danger: { box: 'bg-danger-soft', text: 'text-danger', icon: CircleX },
  success: { box: 'bg-success-soft', text: 'text-success', icon: CircleCheck },
}

const style = computed(() => STYLES[props.tone])
</script>

<template>
  <div
    :class="['flex gap-3 rounded-lg px-4 py-3 text-sm', style.box]"
    :role="tone === 'danger' ? 'alert' : 'status'"
  >
    <component :is="style.icon" :class="['mt-0.5 size-4 shrink-0', style.text]" />
    <div class="min-w-0">
      <p v-if="title" :class="['font-medium', style.text]">{{ title }}</p>
      <div class="text-ink/85"><slot /></div>
    </div>
  </div>
</template>
