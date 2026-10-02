<script setup lang="ts">
import { Check, Copy, Download } from '@lucide/vue'
import { ref } from 'vue'

const props = defineProps<{ text: string; label: string; filename?: string }>()

const copied = ref(false)
const failed = ref(false)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.text)
    copied.value = true
    failed.value = false
    window.setTimeout(() => (copied.value = false), 2000)
  } catch {
    failed.value = true // sin permiso de portapapeles: queda seleccionar a mano
  }
}

function download(): void {
  if (!props.filename) return
  const url = URL.createObjectURL(new Blob([props.text], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = props.filename
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-line">
    <div class="flex items-center justify-between gap-2 border-b border-line bg-subtle px-3 py-1.5">
      <span class="truncate text-xs font-medium text-muted">{{ label }}</span>
      <div class="flex shrink-0 gap-1">
        <button type="button" class="btn-ghost px-2 py-1 text-xs" @click="copy">
          <Check v-if="copied" class="size-3.5 text-success" />
          <Copy v-else class="size-3.5" />
          {{ copied ? 'Copiado' : 'Copiar' }}
        </button>
        <button v-if="filename" type="button" class="btn-ghost px-2 py-1 text-xs" @click="download">
          <Download class="size-3.5" />
          Descargar
        </button>
      </div>
    </div>
    <p v-if="failed" class="bg-warning-soft px-3 py-1.5 text-xs text-warning">
      El navegador no dejó copiar: selecciona el texto y cópialo a mano.
    </p>
    <pre
      class="max-h-80 overflow-auto bg-surface p-3 font-mono text-xs leading-relaxed text-ink"
    ><code>{{ text }}</code></pre>
  </div>
</template>
