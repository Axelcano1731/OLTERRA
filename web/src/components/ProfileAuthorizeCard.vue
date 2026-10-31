<script setup lang="ts">
import { LoaderCircle, PlugZap } from '@lucide/vue'
import { computed, ref } from 'vue'

import { authorizeWithProfiles, type AutofindRow } from '@/api'
import { oltLabel } from '@/lib/oltText'
import { useWrite } from '@/lib/useWrite'

import AlertBox from './AlertBox.vue'

// Alta en las OLT que autorizan con sus perfiles de línea y de servicio (HSGQ, BCD).
const props = defineProps<{
  oltId: string
  row: AutofindRow & { pon: number }
  /** La primera posición libre del PON. */
  freeOnu: number
}>()
const emit = defineEmits<{ finished: [] }>()

const open = ref(false)
const customer = ref('')
const position = ref(props.freeOnu)
const lineProfile = ref(0)
const srvProfile = ref(0)
const submitted = ref(false)
const preview = computed(() => oltLabel(customer.value, 64))

const write = useWrite({
  authorize: {
    busy: 'Autorizando la ONU…',
    ok: 'Listo: la ONU quedó autorizada con el nombre del cliente. Aparece en Clientes en el próximo sondeo.',
  },
})

async function authorize(): Promise<void> {
  submitted.value = true
  if (!preview.value) return
  const ok = await write.run('authorize', () =>
    authorizeWithProfiles(props.oltId, {
      pon: props.row.pon,
      onu: position.value,
      serial: props.row.serial,
      line_profile: lineProfile.value,
      srv_profile: srvProfile.value,
      customer: customer.value.trim(),
    }),
  )
  if (ok) emit('finished')
}
</script>

<template>
  <li class="card p-4">
    <div class="flex flex-wrap items-center gap-3">
      <span class="grid size-9 place-items-center rounded-full bg-accent-soft text-accent">
        <PlugZap class="size-4" />
      </span>
      <span class="min-w-0 flex-1">
        <span class="block font-mono text-sm font-medium">{{ row.serial }}</span>
        <span class="text-xs text-muted">
          PON {{ row.pon }}<template v-if="row.model"> · {{ row.model }}</template>
        </span>
      </span>
      <button v-if="!open" type="button" class="btn-primary" @click="open = true">Autorizar</button>
    </div>

    <form v-if="open" class="mt-4 space-y-4" novalidate @submit.prevent="authorize">
      <div>
        <label :for="`cliente-${row.serial}`" class="label">Nombre del cliente</label>
        <input
          :id="`cliente-${row.serial}`"
          v-model="customer"
          class="input"
          maxlength="80"
          autocomplete="off"
          placeholder="Cristina Gálvis"
        />
        <p v-if="submitted && !preview" class="hint text-danger">Escribe el nombre del cliente.</p>
        <p v-else-if="preview" class="hint">
          En la OLT queda como <span class="code">{{ preview }}</span
          >.
        </p>
      </div>
      <div class="grid gap-4 sm:grid-cols-3">
        <div>
          <label :for="`posicion-${row.serial}`" class="label">Posición en el PON</label>
          <input
            :id="`posicion-${row.serial}`"
            v-model.number="position"
            type="number"
            min="1"
            max="128"
            class="input"
          />
          <p class="hint">La primera libre es la {{ freeOnu }}.</p>
        </div>
        <div>
          <label :for="`linea-${row.serial}`" class="label">Perfil de línea</label>
          <input
            :id="`linea-${row.serial}`"
            v-model.number="lineProfile"
            type="number"
            min="0"
            class="input"
          />
        </div>
        <div>
          <label :for="`servicio-${row.serial}`" class="label">Perfil de servicio</label>
          <input
            :id="`servicio-${row.serial}`"
            v-model.number="srvProfile"
            type="number"
            min="0"
            class="input"
          />
        </div>
      </div>
      <p class="text-xs text-muted">
        La VLAN y la velocidad salen de los perfiles de la OLT (en la de Tocaima, 0 y 0). El Wi‑Fi y
        el PPPoE se configuran en la ONU.
      </p>
      <AlertBox
        v-if="write.state.value"
        :tone="
          write.state.value.tone === 'danger'
            ? 'danger'
            : write.state.value.tone === 'success'
              ? 'success'
              : 'info'
        "
      >
        {{ write.state.value.text }}
      </AlertBox>
      <div v-if="write.state.value?.tone !== 'success'" class="flex justify-end gap-2">
        <button type="button" class="btn-ghost" @click="open = false">Cancelar</button>
        <button type="submit" class="btn-primary" :disabled="write.waiting.value">
          <LoaderCircle v-if="write.waiting.value" class="size-4 animate-spin" />
          Autorizar
        </button>
      </div>
    </form>
  </li>
</template>
