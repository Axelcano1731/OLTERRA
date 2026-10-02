<script setup lang="ts">
import { FileText, Upload, X } from '@lucide/vue'
import { ref, useId } from 'vue'

defineProps<{ label: string; hint: string; accept: string }>()
const files = defineModel<File[]>({ required: true })

const id = useId()
const dragging = ref(false)

function add(list: FileList | null | undefined): void {
  if (!list) return
  const next = [...files.value]
  for (const file of Array.from(list)) {
    if (!next.some((f) => f.name === file.name && f.size === file.size)) next.push(file)
  }
  files.value = next
}

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  add(input.files)
  input.value = '' // permite volver a elegir el mismo archivo
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  add(event.dataTransfer?.files)
}

function remove(index: number): void {
  files.value = files.value.filter((_, i) => i !== index)
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div>
    <p :id="`${id}-label`" class="label">{{ label }}</p>
    <label
      :for="id"
      :class="[
        'flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border border-dashed px-4 py-5 text-center transition-colors',
        dragging ? 'border-accent bg-accent-soft' : 'border-line bg-surface hover:border-accent/60',
      ]"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input
        :id="id"
        type="file"
        class="sr-only"
        multiple
        :accept="accept"
        :aria-labelledby="`${id}-label`"
        @change="onChange"
      />
      <Upload class="size-5 text-muted" />
      <span class="text-sm text-ink">
        <span class="font-medium text-accent">Elige archivos</span> o arrástralos aquí
      </span>
      <span class="text-xs text-muted">{{ hint }}</span>
    </label>
    <ul v-if="files.length" class="mt-2 space-y-1">
      <li
        v-for="(file, index) in files"
        :key="`${file.name}-${file.size}`"
        class="flex items-center justify-between gap-2 rounded-md bg-subtle px-2.5 py-1.5 text-sm"
      >
        <span class="flex min-w-0 items-center gap-2">
          <FileText class="size-4 shrink-0 text-muted" />
          <span class="truncate">{{ file.name }}</span>
          <span class="shrink-0 text-xs text-muted">{{ size(file.size) }}</span>
        </span>
        <button
          type="button"
          class="btn-ghost p-1"
          :aria-label="`Quitar ${file.name}`"
          @click="remove(index)"
        >
          <X class="size-4" />
        </button>
      </li>
    </ul>
  </div>
</template>
