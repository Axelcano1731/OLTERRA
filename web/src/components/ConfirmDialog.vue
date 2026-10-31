<script setup lang="ts">
import { LoaderCircle, X } from '@lucide/vue'
import { onBeforeUnmount, useTemplateRef, watch } from 'vue'

// Diálogo nativo (<dialog> con showModal): Esc lo cierra y el foco queda adentro.
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    confirmLabel?: string
    tone?: 'danger' | 'accent'
    busy?: boolean
    /** Sin botón de confirmar: el contenido trae su propio formulario. */
    hideConfirm?: boolean
  }>(),
  { confirmLabel: 'Confirmar', tone: 'accent', busy: false, hideConfirm: false },
)
const emit = defineEmits<{ close: []; confirm: [] }>()

const dialog = useTemplateRef<HTMLDialogElement>('dialog')

watch(
  () => [props.open, dialog.value] as const,
  ([open, element]) => {
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  },
  { immediate: true },
)
onBeforeUnmount(() => dialog.value?.close())

function onCancel(event: Event): void {
  event.preventDefault() // Esc: lo cierra quien lo abrió, para que el estado no se desfase
  if (!props.busy) emit('close')
}
</script>

<template>
  <dialog
    ref="dialog"
    class="m-auto w-[calc(100%-2rem)] max-w-md rounded-xl border border-line bg-surface p-0 text-ink shadow-xl backdrop:bg-black/40"
    :aria-label="title"
    @cancel="onCancel"
  >
    <div class="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
      <h2 class="font-semibold">{{ title }}</h2>
      <button
        type="button"
        class="rounded-md p-1 text-muted hover:bg-subtle hover:text-ink"
        aria-label="Cerrar"
        :disabled="busy"
        @click="emit('close')"
      >
        <X class="size-4" />
      </button>
    </div>
    <div class="space-y-3 px-5 py-4 text-sm">
      <slot />
    </div>
    <div v-if="!hideConfirm" class="flex justify-end gap-2 border-t border-line px-5 py-3">
      <button type="button" class="btn-ghost" :disabled="busy" @click="emit('close')">
        Cancelar
      </button>
      <button
        type="button"
        :class="tone === 'danger' ? 'btn bg-danger text-white hover:opacity-90' : 'btn-primary'"
        :disabled="busy"
        @click="emit('confirm')"
      >
        <LoaderCircle v-if="busy" class="size-4 animate-spin" />
        {{ confirmLabel }}
      </button>
    </div>
  </dialog>
</template>
