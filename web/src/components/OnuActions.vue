<script setup lang="ts">
import { Copy, Globe, LoaderCircle, Power, Trash } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'

import {
  configureOnu,
  deleteOnu,
  rebootOnu,
  type OnuPort,
  type ProvisionJob,
  type ProvisionTemplate,
} from '@/api'
import { customerPayload, emptyCustomer, forgetSecrets } from '@/lib/customer'
import { errorText } from '@/lib/useAsync'
import { useJob } from '@/lib/useJob'
import { usePlan } from '@/lib/usePlan'

import AlertBox from './AlertBox.vue'
import CustomerServiceFields from './CustomerServiceFields.vue'
import JobProgress from './JobProgress.vue'

const props = defineProps<{ oltId: string; onu: OnuPort; plans: ProvisionTemplate[] }>()
const emit = defineEmits<{ changed: [] }>()

type Panel = 'configure' | 'reboot' | 'delete' | null
const panel = ref<Panel>(null)
const who = computed(() => props.onu.description ?? `la ONU ${props.onu.pon}/${props.onu.onu}`)
const where = computed(() => ({ pon: props.onu.pon, onu: props.onu.onu }))
const idPrefix = computed(() => `onu-${props.onu.pon}-${props.onu.onu}`)

// --- Internet y WiFi ---------------------------------------------------------------------
const planId = ref(props.plans.length === 1 ? (props.plans[0]?.id ?? '') : '')
const plan = computed(() => props.plans.find((item) => item.id === planId.value))
const service = reactive(emptyCustomer())
const fields = ref<{ valid: boolean } | null>(null)
const submitted = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const { job, follow } = useJob()

async function configure(): Promise<void> {
  submitted.value = true
  error.value = null
  if (!plan.value || !fields.value?.valid) return
  busy.value = true
  try {
    const started = await configureOnu(props.oltId, {
      ...where.value,
      ...customerPayload(plan.value, service),
    })
    forgetSecrets(service)
    submitted.value = false
    follow(started, (finished: ProvisionJob) => {
      if (finished.status === 'done') emit('changed')
    })
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

// --- Reiniciar y desautorizar ----------------------------------------------------------------
const action = usePlan()
const actionError = ref<string | null>(null)
const actionKind = ref<'reboot' | 'delete' | null>(null)

async function run(kind: 'reboot' | 'delete'): Promise<void> {
  actionError.value = null
  actionKind.value = kind
  try {
    const plan = await (kind === 'reboot' ? rebootOnu : deleteOnu)(props.oltId, where.value)
    await action.follow(plan.plan_id)
    if (kind === 'delete' && action.plan.value?.status === 'ok') emit('changed')
  } catch (caught) {
    actionError.value = errorText(caught)
  }
}

const actionMessage = computed(() => {
  const plan = action.plan.value
  if (!plan || action.waiting.value) return null
  const failed = plan.result?.outputs?.find((output) => !output.ok)
  if (plan.status === 'ok') {
    return actionKind.value === 'reboot'
      ? { tone: 'success' as const, text: `Listo: la ONU de ${who.value} se está reiniciando.` }
      : { tone: 'success' as const, text: `Listo: la ONU de ${who.value} quedó desautorizada.` }
  }
  return {
    tone: 'danger' as const,
    text: `No se pudo: ${failed?.error ?? plan.result?.error ?? 'la OLT no respondió'}`,
  }
})

function toggle(next: Panel): void {
  panel.value = panel.value === next ? null : next
  actionError.value = null
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap gap-1.5">
      <button type="button" class="btn-ghost px-2.5 py-1 text-xs" @click="toggle('configure')">
        <Globe class="size-3.5" />
        Internet y WiFi
      </button>
      <button type="button" class="btn-ghost px-2.5 py-1 text-xs" @click="toggle('reboot')">
        <Power class="size-3.5" />
        Reiniciar
      </button>
      <RouterLink
        :to="{ name: 'template-new', query: { olt: oltId, pon: onu.pon, onu: onu.onu } }"
        class="btn-ghost px-2.5 py-1 text-xs"
      >
        <Copy class="size-3.5" />
        Copiar como plan
      </RouterLink>
      <button
        type="button"
        class="btn-ghost px-2.5 py-1 text-xs text-danger"
        @click="toggle('delete')"
      >
        <Trash class="size-3.5" />
        Desautorizar
      </button>
    </div>

    <form
      v-if="panel === 'configure' && !job"
      class="space-y-4 rounded-lg border border-line p-4"
      novalidate
      @submit.prevent="configure"
    >
      <p class="text-sm text-muted">
        Pone o cambia el PPPoE y el WiFi de {{ who }}. La ONU tiene que estar conectada.
      </p>
      <div class="grid gap-4 sm:grid-cols-2">
        <div class="sm:col-span-2">
          <label :for="`${idPrefix}-plan`" class="label">Plan</label>
          <select :id="`${idPrefix}-plan`" v-model="planId" class="input">
            <option value="">Elige el plan del cliente</option>
            <option v-for="item in plans" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
          <p v-if="submitted && !plan" class="hint text-danger">Elige un plan.</p>
        </div>
        <CustomerServiceFields
          ref="fields"
          v-model="service"
          :plan="plan"
          :show-errors="submitted"
          :id-prefix="idPrefix"
        />
      </div>
      <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>
      <div class="flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy">
          <LoaderCircle v-if="busy" class="size-4 animate-spin" />
          Configurar
        </button>
      </div>
    </form>
    <JobProgress v-if="panel === 'configure' && job" :job="job" />

    <div
      v-if="panel === 'reboot' || panel === 'delete'"
      class="flex flex-wrap items-center gap-3 rounded-lg border border-line p-4"
    >
      <p class="flex-1 text-sm">
        <template v-if="panel === 'reboot'">
          ¿Reiniciar la ONU de <b>{{ who }}</b
          >? Se queda sin servicio un par de minutos.
        </template>
        <template v-else>
          ¿Desautorizar la ONU de <b>{{ who }}</b
          >? Se borra su configuración en la OLT y el cliente se queda sin servicio.
        </template>
      </p>
      <button
        type="button"
        :class="[
          'btn',
          panel === 'delete' ? 'bg-danger text-on-accent hover:opacity-90' : 'btn-secondary',
        ]"
        :disabled="action.waiting.value"
        @click="run(panel)"
      >
        <LoaderCircle v-if="action.waiting.value" class="size-4 animate-spin" />
        {{ panel === 'reboot' ? 'Sí, reiniciar' : 'Sí, desautorizar' }}
      </button>
    </div>
    <AlertBox v-if="actionError" tone="danger">{{ actionError }}</AlertBox>
    <AlertBox v-else-if="actionMessage" :tone="actionMessage.tone">
      {{ actionMessage.text }}
    </AlertBox>
  </div>
</template>
