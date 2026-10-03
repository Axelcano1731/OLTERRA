<script setup lang="ts">
import { LoaderCircle, Trash } from '@lucide/vue'
import { ref } from 'vue'

import { deleteOlt, type Olt } from '@/api'
import { errorText } from '@/lib/useAsync'

// compact: en la tabla de la lista; el aviso largo no cabe y va como tooltip.
const props = defineProps<{ olt: Olt; compact?: boolean }>()
const emit = defineEmits<{ deleted: [olt: Olt]; failed: [message: string] }>()

const confirming = ref(false)
const busy = ref(false)

async function remove(): Promise<void> {
  busy.value = true
  try {
    await deleteOlt(props.olt.id)
    emit('deleted', props.olt)
  } catch (caught) {
    emit('failed', errorText(caught))
  } finally {
    busy.value = false
    confirming.value = false
  }
}
</script>

<template>
  <span
    v-if="confirming"
    class="inline-flex items-center justify-end gap-1.5"
    title="Se borran su credencial y su historial de consultas"
  >
    <span class="text-xs text-danger">
      {{
        compact
          ? '¿Eliminar?'
          : `¿Eliminar ${olt.name}? Se borran su credencial y su historial de consultas.`
      }}
    </span>
    <button
      type="button"
      class="btn px-2.5 py-1 text-xs bg-danger text-on-accent hover:opacity-90"
      :disabled="busy"
      @click="remove"
    >
      <LoaderCircle v-if="busy" class="size-3.5 animate-spin" />
      Sí, eliminar
    </button>
    <button type="button" class="btn-ghost px-2 py-1 text-xs" @click="confirming = false">
      Cancelar
    </button>
  </span>
  <button
    v-else
    type="button"
    class="btn-ghost px-2 py-1 text-xs text-danger"
    :aria-label="`Eliminar ${olt.name}`"
    @click="confirming = true"
  >
    <Trash class="size-3.5" />
    Eliminar
  </button>
</template>
