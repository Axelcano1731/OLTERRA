<script setup lang="ts">
import { LoaderCircle, Power, RadioTower, Search, Trash, TriangleAlert } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'

import {
  authorizeOnu,
  deleteOnu,
  getOlt,
  listTemplates,
  queryOlt,
  rebootOnu,
  type AuthorizeRequest,
  type WritePlan,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import PageHeader from '@/components/PageHeader.vue'
import PlanResult from '@/components/PlanResult.vue'
import { nextFreeOnu } from '@/lib/templates'
import { errorText, useAsync } from '@/lib/useAsync'
import { usePlan } from '@/lib/usePlan'

const props = defineProps<{ id: string }>()

interface AutofindRow {
  pon: number | null
  serial: string
  index: number | null
  model: string | null
}

const olt = useAsync(() => getOlt(props.id))
const templates = useAsync(listTemplates)

// --- 1. Buscar ONU sin autorizar ----------------------------------------------------------
const searchPon = ref(1)
const search = usePlan()
const searchError = ref<string | null>(null)

async function find(): Promise<void> {
  searchError.value = null
  try {
    const plan = await queryOlt(props.id, {
      commands: ['onu.autofind', 'onu.list'],
      pon: [searchPon.value],
      onu: [],
    })
    await search.follow(plan.plan_id)
  } catch (caught) {
    searchError.value = errorText(caught)
  }
}

function outputOf(key: string) {
  return search.plan.value?.result?.outputs?.find((item) => item.key === key)
}

const found = computed<AutofindRow[] | null>(() => {
  const output = outputOf('onu.autofind')
  return output?.ok && Array.isArray(output.data) ? (output.data as AutofindRow[]) : null
})

const freeIndex = computed<number | null>(() => {
  const output = outputOf('onu.list')
  if (!output?.ok || !Array.isArray(output.data)) return null
  return nextFreeOnu((output.data as { onu: number }[]).map((row) => row.onu))
})

// --- 2. Alta ------------------------------------------------------------------------------
const form = reactive({
  templateId: '',
  pon: 1,
  onu: 1,
  serial: '',
  description: '',
  pppoeUser: '',
  pppoePassword: '',
  wifiSsid: '',
  wifiKey: '',
})

const template = computed(() => templates.data.value?.find((item) => item.id === form.templateId))

function use(row: AutofindRow): void {
  form.serial = row.serial
  form.pon = row.pon ?? searchPon.value
  if (freeIndex.value !== null) form.onu = freeIndex.value
}

watch(freeIndex, (index) => {
  if (index !== null && !form.serial) form.onu = index
})

const SERIAL = /^([A-Za-z0-9]{4}[0-9A-Fa-f]{8}|[0-9A-Fa-f]{16})$/
const DESCRIPTION = /^[A-Za-z0-9_.-]{1,64}$/
const PPPOE_USER = /^[A-Za-z0-9_.@-]{1,64}$/
const SSID = /^[A-Za-z0-9_.-]{1,32}$/
const CLI_SECRET = (low: number) => new RegExp(`^[!-~]{${low},63}$`)

const submitted = ref(false)
const problems = computed(() => {
  const found: Record<string, string> = {}
  if (!form.templateId) found.templateId = 'Elige una plantilla.'
  if (!SERIAL.test(form.serial.trim())) found.serial = 'Serial GPON: VSOL0008D09C.'
  if (!DESCRIPTION.test(form.description)) {
    found.description = 'Sin espacios: letras, números, punto, guion o guion bajo.'
  }
  if (form.onu < 1 || form.onu > 128) found.onu = 'De 1 a 128.'
  if (form.pon < 1 || form.pon > 16) found.pon = 'De 1 a 16.'
  if (template.value?.body.wan) {
    if (!PPPOE_USER.test(form.pppoeUser)) found.pppoeUser = 'Usuario PPPoE sin espacios.'
    if (!CLI_SECRET(1).test(form.pppoePassword) || form.pppoePassword.includes('?')) {
      found.pppoePassword = 'Sin espacios ni signo de pregunta.'
    }
  }
  if (template.value?.body.wifi && (form.wifiSsid || form.wifiKey)) {
    if (!SSID.test(form.wifiSsid)) found.wifiSsid = 'SSID sin espacios, hasta 32 caracteres.'
    if (!CLI_SECRET(8).test(form.wifiKey) || form.wifiKey.includes('?')) {
      found.wifiKey = 'De 8 a 63 caracteres, sin espacios ni signo de pregunta.'
    }
  }
  return found
})

function show(field: string): string | undefined {
  return submitted.value ? problems.value[field] : undefined
}

const alta = usePlan()
const written = ref<WritePlan | null>(null)
const busy = ref(false)
const altaError = ref<string | null>(null)

async function authorize(): Promise<void> {
  submitted.value = true
  altaError.value = null
  if (Object.keys(problems.value).length) return
  const body: AuthorizeRequest = {
    template_id: form.templateId,
    pon: form.pon,
    onu: form.onu,
    serial: form.serial.trim().toUpperCase(),
    description: form.description,
  }
  if (template.value?.body.wan) {
    body.pppoe_user = form.pppoeUser
    body.pppoe_password = form.pppoePassword
  }
  if (template.value?.body.wifi && form.wifiSsid) {
    body.wifi_ssid = form.wifiSsid
    body.wifi_key = form.wifiKey
  }
  busy.value = true
  try {
    written.value = await authorizeOnu(props.id, body)
    // Las claves ya viajaron selladas: no se quedan en la pantalla.
    form.pppoePassword = ''
    form.wifiKey = ''
    submitted.value = false
    await alta.follow(written.value.plan_id)
  } catch (caught) {
    altaError.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

// --- 3. Reiniciar o borrar ----------------------------------------------------------------
const target = reactive({ pon: 1, onu: 1 })
const action = usePlan()
const actionPlan = ref<WritePlan | null>(null)
const actionError = ref<string | null>(null)
const confirmDelete = ref('')
const targetLabel = computed(() => `${target.pon}:${target.onu}`)

async function run(kind: 'reboot' | 'delete'): Promise<void> {
  actionError.value = null
  try {
    const call = kind === 'reboot' ? rebootOnu : deleteOnu
    actionPlan.value = await call(props.id, { pon: target.pon, onu: target.onu })
    confirmDelete.value = ''
    await action.follow(actionPlan.value.plan_id)
  } catch (caught) {
    actionError.value = errorText(caught)
  }
}
</script>

<template>
  <PageHeader
    title="Aprovisionar"
    :description="`Autorizar, reiniciar o borrar ONU en ${olt.data.value?.name ?? 'la OLT'}. Cada cambio se detiene al primer error, se guarda en la OLT y queda en la bitácora.`"
    :back="{ name: 'olt', params: { id } }"
    :back-label="olt.data.value?.name ?? 'OLT'"
  />

  <div class="space-y-6">
    <section class="card p-5 sm:p-6">
      <div class="flex items-center gap-2">
        <Search class="size-4 text-muted" />
        <h2 class="font-semibold">1. ONU sin autorizar</h2>
      </div>
      <p class="mt-1 text-sm text-muted">
        Busca en un PON las ONU conectadas que la OLT todavía no autoriza (autofind).
      </p>
      <div class="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label for="search-pon" class="label">PON</label>
          <input
            id="search-pon"
            v-model.number="searchPon"
            type="number"
            min="1"
            max="16"
            class="input w-24"
          />
        </div>
        <button type="button" class="btn-secondary" :disabled="search.waiting.value" @click="find">
          <LoaderCircle v-if="search.waiting.value" class="size-4 animate-spin" />
          <Search v-else class="size-4" />
          Buscar
        </button>
      </div>
      <AlertBox v-if="searchError || search.error.value" tone="danger" class="mt-4">
        {{ searchError ?? search.error.value }}
      </AlertBox>
      <template v-if="search.plan.value && !search.waiting.value">
        <p v-if="found && !found.length" class="mt-4 text-sm text-muted">
          No hay ONU sin autorizar en el PON {{ searchPon }}.
        </p>
        <ul v-else-if="found" class="mt-4 divide-y divide-line rounded-lg border border-line">
          <li
            v-for="row in found"
            :key="row.serial"
            class="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
          >
            <span class="font-mono text-sm">{{ row.serial }}</span>
            <span class="text-sm text-muted">{{ row.model ?? '' }}</span>
            <button type="button" class="btn-ghost px-2 py-1 text-xs" @click="use(row)">
              Usar
            </button>
          </li>
        </ul>
        <details v-else class="mt-4">
          <summary class="cursor-pointer text-sm text-muted">
            La salida no se pudo interpretar: ver lo que respondió la OLT
          </summary>
          <PlanResult :plan="search.plan.value" class="mt-3" />
        </details>
        <p v-if="freeIndex !== null" class="mt-3 text-sm text-muted">
          Siguiente índice libre en el PON {{ searchPon }}: <b>{{ freeIndex }}</b
          >.
        </p>
      </template>
    </section>

    <form class="card p-5 sm:p-6" novalidate @submit.prevent="authorize">
      <div class="flex items-center gap-2">
        <RadioTower class="size-4 text-muted" />
        <h2 class="font-semibold">2. Autorizar</h2>
      </div>
      <div class="mt-4 grid gap-4 sm:grid-cols-4">
        <div class="sm:col-span-2">
          <label for="template" class="label">Plantilla</label>
          <select
            id="template"
            v-model="form.templateId"
            class="input"
            :aria-invalid="!!show('templateId')"
          >
            <option value="">Elige una plantilla</option>
            <option v-for="item in templates.data.value ?? []" :key="item.id" :value="item.id">
              {{ item.name }}
            </option>
          </select>
          <p v-if="show('templateId')" class="hint text-danger">{{ show('templateId') }}</p>
          <p v-else-if="templates.data.value?.length === 0" class="hint">
            No hay plantillas:
            <RouterLink :to="{ name: 'template-new' }" class="underline">crea una</RouterLink>.
          </p>
        </div>
        <div>
          <label for="pon" class="label">PON</label>
          <input id="pon" v-model.number="form.pon" type="number" min="1" max="16" class="input" />
          <p v-if="show('pon')" class="hint text-danger">{{ show('pon') }}</p>
        </div>
        <div>
          <label for="onu" class="label">Índice de ONU</label>
          <input id="onu" v-model.number="form.onu" type="number" min="1" max="128" class="input" />
          <p v-if="show('onu')" class="hint text-danger">{{ show('onu') }}</p>
        </div>
        <div class="sm:col-span-2">
          <label for="serial" class="label">Serial</label>
          <input
            id="serial"
            v-model="form.serial"
            class="input font-mono uppercase"
            placeholder="VSOL0008D09C"
            :aria-invalid="!!show('serial')"
          />
          <p v-if="show('serial')" class="hint text-danger">{{ show('serial') }}</p>
        </div>
        <div class="sm:col-span-2">
          <label for="description" class="label">Descripción</label>
          <input
            id="description"
            v-model="form.description"
            class="input"
            placeholder="CLIENTE-1234"
            maxlength="64"
            :aria-invalid="!!show('description')"
          />
          <p v-if="show('description')" class="hint text-danger">{{ show('description') }}</p>
          <p v-else class="hint">Lo que se ve en la OLT; sin espacios.</p>
        </div>
        <template v-if="template?.body.wan">
          <div class="sm:col-span-2">
            <label for="pppoe-user" class="label">Usuario PPPoE</label>
            <input
              id="pppoe-user"
              v-model="form.pppoeUser"
              class="input"
              autocomplete="off"
              :aria-invalid="!!show('pppoeUser')"
            />
            <p v-if="show('pppoeUser')" class="hint text-danger">{{ show('pppoeUser') }}</p>
          </div>
          <div class="sm:col-span-2">
            <label for="pppoe-password" class="label">Clave PPPoE</label>
            <input
              id="pppoe-password"
              v-model="form.pppoePassword"
              type="password"
              class="input"
              autocomplete="new-password"
              :aria-invalid="!!show('pppoePassword')"
            />
            <p v-if="show('pppoePassword')" class="hint text-danger">
              {{ show('pppoePassword') }}
            </p>
          </div>
        </template>
        <template v-if="template?.body.wifi">
          <div class="sm:col-span-2">
            <label for="ssid" class="label">
              SSID <span class="text-muted">(opcional)</span>
            </label>
            <input
              id="ssid"
              v-model="form.wifiSsid"
              class="input"
              autocomplete="off"
              :aria-invalid="!!show('wifiSsid')"
            />
            <p v-if="show('wifiSsid')" class="hint text-danger">{{ show('wifiSsid') }}</p>
          </div>
          <div class="sm:col-span-2">
            <label for="wifi-key" class="label">
              Clave WiFi <span class="text-muted">(opcional)</span>
            </label>
            <input
              id="wifi-key"
              v-model="form.wifiKey"
              type="password"
              class="input"
              autocomplete="new-password"
              :aria-invalid="!!show('wifiKey')"
            />
            <p v-if="show('wifiKey')" class="hint text-danger">{{ show('wifiKey') }}</p>
          </div>
        </template>
      </div>
      <p class="mt-4 text-sm text-muted">
        Las claves viajan cifradas solo hasta el ejecutor que habla con la OLT: Olterra no las
        guarda ni las muestra.
      </p>
      <AlertBox v-if="altaError" tone="danger" class="mt-4">{{ altaError }}</AlertBox>
      <div class="mt-4 flex justify-end">
        <button type="submit" class="btn-primary" :disabled="busy || alta.waiting.value">
          <LoaderCircle v-if="busy || alta.waiting.value" class="size-4 animate-spin" />
          <RadioTower v-else class="size-4" />
          Autorizar ONU
        </button>
      </div>
    </form>

    <section v-if="written" class="card p-5 sm:p-6">
      <h2 class="font-semibold">Resultado del alta</h2>
      <AlertBox
        v-if="written.unverified.length"
        tone="warning"
        title="Modo laboratorio"
        class="mt-4"
      >
        Corrieron comandos todavía sin validar en este modelo: {{ written.unverified.join(', ') }}.
      </AlertBox>
      <p v-if="alta.waiting.value" class="mt-4 text-sm text-muted">
        Esperando a la OLT{{ alta.slow.value ? ' (está tardando)' : '' }}…
      </p>
      <AlertBox v-if="alta.error.value" tone="danger" class="mt-4">{{ alta.error.value }}</AlertBox>
      <PlanResult v-if="alta.plan.value" :plan="alta.plan.value" class="mt-4" />
      <p v-if="alta.plan.value?.status === 'ok'" class="mt-4 text-sm text-muted">
        La ONU tarda hasta un minuto en quedar "working". Vuelve a buscar en el PON o consulta su
        estado desde la OLT.
      </p>
    </section>

    <section class="card p-5 sm:p-6">
      <div class="flex items-center gap-2">
        <Power class="size-4 text-muted" />
        <h2 class="font-semibold">3. Reiniciar o borrar una ONU</h2>
      </div>
      <div class="mt-4 flex flex-wrap items-end gap-3">
        <div>
          <label for="target-pon" class="label">PON</label>
          <input
            id="target-pon"
            v-model.number="target.pon"
            type="number"
            min="1"
            max="16"
            class="input w-24"
          />
        </div>
        <div>
          <label for="target-onu" class="label">ONU</label>
          <input
            id="target-onu"
            v-model.number="target.onu"
            type="number"
            min="1"
            max="128"
            class="input w-24"
          />
        </div>
        <button
          type="button"
          class="btn-secondary"
          :disabled="action.waiting.value"
          @click="run('reboot')"
        >
          <Power class="size-4" />
          Reiniciar
        </button>
      </div>
      <div class="mt-4 rounded-lg border border-line p-4">
        <p class="flex items-center gap-2 text-sm font-medium text-danger">
          <TriangleAlert class="size-4" />
          Borrar desautoriza la ONU y elimina su configuración en la OLT. El cliente se queda sin
          servicio.
        </p>
        <div class="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label for="confirm-delete" class="label">
              Escribe <span class="font-mono">{{ targetLabel }}</span> para confirmar
            </label>
            <input id="confirm-delete" v-model="confirmDelete" class="input w-40 font-mono" />
          </div>
          <button
            type="button"
            class="btn bg-danger text-on-accent hover:opacity-90"
            :disabled="confirmDelete !== targetLabel || action.waiting.value"
            @click="run('delete')"
          >
            <Trash class="size-4" />
            Borrar ONU
          </button>
        </div>
      </div>
      <AlertBox v-if="actionError" tone="danger" class="mt-4">{{ actionError }}</AlertBox>
      <AlertBox
        v-if="actionPlan?.unverified.length"
        tone="warning"
        title="Modo laboratorio"
        class="mt-4"
      >
        Corrieron comandos todavía sin validar: {{ actionPlan.unverified.join(', ') }}.
      </AlertBox>
      <PlanResult v-if="action.plan.value" :plan="action.plan.value" class="mt-4" />
    </section>
  </div>
</template>
