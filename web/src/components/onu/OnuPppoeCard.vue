<script setup lang="ts">
import { LoaderCircle, Pencil, Router } from '@lucide/vue'
import { ref } from 'vue'

import { setOnuPppoeUser, type OnuStatus } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import StatusPill from '@/components/StatusPill.vue'
import { timeAgo } from '@/lib/format'
import { formatBps } from '@/lib/traffic'
import { errorText } from '@/lib/useAsync'
import { can } from '@/session'

// La sesión PPPoE del cliente en el MikroTik: sirve para las tres marcas. Olterra la busca por el
// nombre del cliente; si el usuario PPPoE se llama distinto, se pone a mano.
const props = defineProps<{ status: OnuStatus }>()
const emit = defineEmits<{ changed: [status: OnuStatus] }>()

const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const error = ref<string | null>(null)

function edit(): void {
  draft.value = props.status.pppoe_user ?? ''
  error.value = null
  editing.value = true
}

async function save(): Promise<void> {
  saving.value = true
  error.value = null
  try {
    const user = draft.value.trim() || null
    const saved = await setOnuPppoeUser(
      props.status.olt_id,
      props.status.pon,
      props.status.onu,
      user,
    )
    emit('changed', saved)
    editing.value = false
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="card p-5">
    <div class="mb-3 flex items-center justify-between gap-2">
      <h3 class="flex items-center gap-2 font-semibold">
        <Router class="size-4 text-muted" /> Sesión en el MikroTik
      </h3>
      <StatusPill v-if="status.pppoe" tone="success">Conectada</StatusPill>
      <StatusPill v-else-if="status.pppoe_user" tone="warning">Sin sesión</StatusPill>
    </div>

    <AlertBox
      v-if="status.pppoe && !status.online"
      tone="warning"
      title="El PPPoE está activo pero la ONU está caída"
      class="mb-3"
    >
      El cliente navega por otro equipo: puede que le cambiaran la ONU y esta quedó registrada
      apagada, o que esté conectado por otra OLT.
    </AlertBox>

    <form v-if="editing" class="space-y-2" @submit.prevent="save">
      <label for="usuario-pppoe" class="label">Usuario PPPoE</label>
      <div class="flex gap-2">
        <input
          id="usuario-pppoe"
          v-model="draft"
          class="input font-mono"
          autocomplete="off"
          spellcheck="false"
          placeholder="como está en el MikroTik"
        />
        <button type="submit" class="btn-primary" :disabled="saving">
          <LoaderCircle v-if="saving" class="size-4 animate-spin" />
          Guardar
        </button>
        <button type="button" class="btn-ghost" @click="editing = false">Cancelar</button>
      </div>
      <p class="hint">Vacío: Olterra lo vuelve a buscar por el nombre del cliente.</p>
      <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>
    </form>

    <template v-else>
      <p v-if="!status.pppoe_user" class="text-sm text-muted">
        Olterra no encontró en el MikroTik un usuario PPPoE con el nombre de este cliente. Ponlo a
        mano para ver su sesión, su IP y, en las OLT que no dan el tráfico (BCD), su consumo.
      </p>
      <dl v-else class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt class="text-muted">Usuario</dt>
        <dd class="font-mono">
          {{ status.pppoe_user }}
          <span class="font-sans text-xs text-muted">
            ({{ status.pppoe_user_source === 'manual' ? 'puesto a mano' : 'por el nombre' }})
          </span>
        </dd>
        <template v-if="status.pppoe">
          <dt class="text-muted">IP</dt>
          <dd class="font-mono">{{ status.pppoe.address ?? '—' }}</dd>
          <dt class="text-muted">MAC</dt>
          <dd class="font-mono">{{ status.pppoe.mac ?? '—' }}</dd>
          <dt class="text-muted">Conectada hace</dt>
          <dd>{{ status.pppoe.uptime ?? '—' }}</dd>
          <dt class="text-muted">Plan (cola)</dt>
          <dd>
            <template v-if="status.pppoe.plan_down_bps">
              ↓ {{ formatBps(status.pppoe.plan_down_bps) }} · ↑
              {{ formatBps(status.pppoe.plan_up_bps) }}
            </template>
            <template v-else>sin cola</template>
          </dd>
          <dt class="text-muted">Leída</dt>
          <dd>{{ timeAgo(status.pppoe.seen_at) }}</dd>
        </template>
        <template v-else>
          <dt class="text-muted">Estado</dt>
          <dd>no hay una sesión con ese usuario en el MikroTik</dd>
        </template>
      </dl>
      <button v-if="can('onu:write')" type="button" class="btn-secondary mt-4" @click="edit">
        <Pencil class="size-4" />
        {{ status.pppoe_user ? 'Cambiar usuario PPPoE' : 'Poner usuario PPPoE' }}
      </button>
    </template>
  </section>
</template>
