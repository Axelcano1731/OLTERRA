<script setup lang="ts">
import { LoaderCircle, RadioTower, RotateCcw } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'

import {
  authorizeOnu,
  configureOnu,
  type AutofindRow,
  type ProvisionJob,
  type ProvisionTemplate,
} from '@/api'
import { customerPayload, emptyCustomer, forgetSecrets } from '@/lib/customer'
import { oltLabel, realModel } from '@/lib/oltText'
import { errorText } from '@/lib/useAsync'
import { useJob } from '@/lib/useJob'

import AlertBox from './AlertBox.vue'
import CustomerServiceFields from './CustomerServiceFields.vue'
import JobProgress from './JobProgress.vue'

const props = defineProps<{
  oltId: string
  row: AutofindRow & { pon: number }
  plans: ProvisionTemplate[]
}>()
const emit = defineEmits<{ finished: [job: ProvisionJob] }>()

const EQUIPMENT_ID = /^[A-Za-z0-9_.-]{1,32}$/
// "NULL" en el autofind: la OLT todavía no sabe el modelo. El alta lo lee de la ONU conectada.
const model = computed(() => realModel(props.row.model))

const open = ref(false)
const customer = ref('')
const planId = ref(props.plans.length === 1 ? (props.plans[0]?.id ?? '') : '')
const service = reactive(emptyCustomer())
const fields = ref<{ valid: boolean } | null>(null)
const submitted = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const { job, error: jobError, follow, stop } = useJob()
// La ONU ya quedó autorizada y falta internet/WiFi: el reintento solo hace eso.
const resume = ref<{ pon: number; onu: number } | null>(null)

const plan = computed(() => props.plans.find((item) => item.id === planId.value))
const preview = computed(() => oltLabel(customer.value, 64))
const idPrefix = computed(() => `alta-${props.row.serial}`)

async function authorize(): Promise<void> {
  submitted.value = true
  error.value = null
  if ((!resume.value && !preview.value) || !plan.value || !fields.value?.valid) return
  busy.value = true
  try {
    const started = resume.value
      ? await configureOnu(props.oltId, {
          ...resume.value,
          ...customerPayload(plan.value, service),
        })
      : await authorizeOnu(props.oltId, {
          pon: props.row.pon,
          serial: props.row.serial,
          customer: customer.value.trim(),
          ...(model.value && EQUIPMENT_ID.test(model.value) ? { equipment_id: model.value } : {}),
          ...customerPayload(plan.value, service),
        })
    forgetSecrets(service)
    submitted.value = false
    follow(started, (finished) => emit('finished', finished))
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

function retry(): void {
  const failed = job.value
  const authorized = failed?.steps.find((step) => step.key === 'authorize')?.status === 'done'
  resume.value =
    failed && (failed.kind === 'configure' || authorized) && failed.pon && failed.onu
      ? { pon: failed.pon, onu: failed.onu }
      : null
  stop()
  job.value = null
  open.value = true
}
</script>

<template>
  <li class="rounded-lg border border-line bg-surface">
    <div class="flex flex-wrap items-center gap-3 px-4 py-3">
      <span class="grid size-9 place-items-center rounded-full bg-accent-soft text-accent">
        <RadioTower class="size-4" />
      </span>
      <div class="min-w-0 flex-1">
        <p class="font-mono text-sm font-medium">{{ row.serial }}</p>
        <p class="text-xs text-muted">{{ model ?? 'ONU' }} · PON {{ row.pon }}</p>
      </div>
      <button v-if="!open && !job" type="button" class="btn-primary" @click="open = true">
        Autorizar
      </button>
    </div>

    <form
      v-if="open && !job"
      class="space-y-4 border-t border-line px-4 py-4"
      novalidate
      @submit.prevent="authorize"
    >
      <AlertBox v-if="resume" tone="info">
        La ONU ya quedó autorizada (PON {{ resume.pon }}, posición {{ resume.onu }}). Falta internet
        y WiFi: vuelve a escribir la clave PPPoE.
      </AlertBox>
      <div class="grid gap-4 sm:grid-cols-2">
        <div v-if="!resume" class="sm:col-span-2">
          <label :for="`${idPrefix}-customer`" class="label">Nombre del cliente</label>
          <input
            :id="`${idPrefix}-customer`"
            v-model="customer"
            class="input"
            maxlength="80"
            placeholder="Juan Pérez"
            autocomplete="off"
            :aria-invalid="submitted && !preview"
          />
          <p v-if="submitted && !preview" class="hint text-danger">
            Escribe el nombre del cliente.
          </p>
          <p v-else-if="preview && preview !== customer.trim()" class="hint">
            En la OLT quedará como <b class="font-mono">{{ preview }}</b
            >.
          </p>
        </div>
        <div class="sm:col-span-2">
          <label :for="`${idPrefix}-plan`" class="label">Plan</label>
          <select
            :id="`${idPrefix}-plan`"
            v-model="planId"
            class="input"
            :aria-invalid="submitted && !plan"
          >
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
      <div class="flex justify-end gap-2">
        <button type="button" class="btn-secondary" @click="open = false">Cancelar</button>
        <button type="submit" class="btn-primary" :disabled="busy">
          <LoaderCircle v-if="busy" class="size-4 animate-spin" />
          {{ resume ? 'Configurar internet' : 'Autorizar y configurar' }}
        </button>
      </div>
    </form>

    <div v-if="job" class="space-y-3 border-t border-line px-4 py-4">
      <JobProgress :job="job" />
      <p v-if="jobError" class="text-xs text-muted">Reintentando la consulta… ({{ jobError }})</p>
      <button v-if="job.status === 'failed'" type="button" class="btn-secondary" @click="retry">
        <RotateCcw class="size-4" />
        Intentar de nuevo
      </button>
    </div>
  </li>
</template>
