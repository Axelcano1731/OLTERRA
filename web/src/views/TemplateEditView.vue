<script setup lang="ts">
import { Braces, Copy, LoaderCircle, Save, ShieldCheck, SlidersHorizontal } from '@lucide/vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  createTemplate,
  getTemplate,
  listOlts,
  queryOlt,
  updateTemplate,
  type OnuRunningConfig,
  type TemplateBody,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import PageHeader from '@/components/PageHeader.vue'
import {
  FIREWALL_LEVELS,
  UNI_PORTS,
  WAN_SERVICES,
  bodyToSimple,
  emptySimple,
  simpleToBody,
} from '@/lib/templates'
import { errorText, useAsync } from '@/lib/useAsync'
import { usePlan } from '@/lib/usePlan'

const props = defineProps<{ id?: string }>()
const router = useRouter()
const route = useRoute()

const NAME = /^[A-Za-z0-9_. -]{1,48}$/
const PROFILE = /^[A-Za-z0-9_.-]{1,32}$/

const name = ref('')
const simple = reactive(emptySimple())
const mode = ref<'simple' | 'json'>('simple')
const json = ref('')
const loadError = ref<string | null>(null)
const saving = ref(false)
const saveError = ref<string | null>(null)
const submitted = ref(false)

const olts = useAsync(listOlts)
const copy = reactive({ oltId: '', pon: 1, onu: 1 })
const copyPlan = usePlan()
const copyError = ref<string | null>(null)
const copied = ref<OnuRunningConfig | null>(null)

function setBody(body: TemplateBody): void {
  const short = bodyToSimple(body)
  json.value = JSON.stringify(body, null, 2)
  if (short) {
    Object.assign(simple, short)
    mode.value = 'simple'
  } else {
    mode.value = 'json'
  }
}

onMounted(async () => {
  // "Copiar como plan" desde la lista de clientes: llega con la ONU y se lee sola.
  const { olt, pon, onu } = route.query
  if (!props.id && typeof olt === 'string' && Number(pon) > 0 && Number(onu) > 0) {
    copy.oltId = olt
    copy.pon = Number(pon)
    copy.onu = Number(onu)
    void readOnu()
    return
  }
  if (!props.id) return
  try {
    const template = await getTemplate(props.id)
    name.value = template.name
    setBody(template.body)
  } catch (caught) {
    loadError.value = errorText(caught)
  }
})

function switchMode(next: 'simple' | 'json'): void {
  if (next === 'json') {
    json.value = JSON.stringify(simpleToBody(simple), null, 2)
    mode.value = 'json'
    return
  }
  try {
    const short = bodyToSimple(JSON.parse(json.value) as TemplateBody)
    if (!short) {
      saveError.value =
        'Este plan tiene más de una VLAN, T-CONT o GEM: solo se puede editar como JSON.'
      return
    }
    Object.assign(simple, short)
    mode.value = 'simple'
  } catch {
    saveError.value = 'El JSON no es válido.'
  }
}

const problems = computed(() => {
  const found: Record<string, string> = {}
  if (!NAME.test(name.value.trim())) {
    found.name = 'De 1 a 48 caracteres: letras, números, espacio, punto o guion.'
  }
  if (mode.value === 'simple') {
    if (!Number.isInteger(simple.vlan) || simple.vlan < 1 || simple.vlan > 4094) {
      found.vlan = 'De 1 a 4094.'
    }
    if (!PROFILE.test(simple.authProfile)) found.authProfile = 'Nombre de perfil sin espacios.'
    if (simple.onuProfile && !PROFILE.test(simple.onuProfile)) found.onuProfile = 'Sin espacios.'
    if (!PROFILE.test(simple.dba)) found.dba = 'Nombre de perfil DBA sin espacios.'
    if (simple.limitDown && !PROFILE.test(simple.limitDown)) found.limitDown = 'Sin espacios.'
    if (simple.pppoe && simple.binds.length === 0) found.binds = 'Elige al menos un puerto.'
    if (simple.pppoe && (simple.mtu < 576 || simple.mtu > 1500)) found.mtu = 'De 576 a 1500.'
  }
  return found
})

function show(field: string): string | undefined {
  return submitted.value ? problems.value[field] : undefined
}

function body(): TemplateBody {
  return mode.value === 'simple' ? simpleToBody(simple) : (JSON.parse(json.value) as TemplateBody)
}

async function save(): Promise<void> {
  submitted.value = true
  saveError.value = null
  if (Object.keys(problems.value).length > 0) return
  let payload: TemplateBody
  try {
    payload = body()
  } catch {
    saveError.value = 'El JSON no es válido.'
    return
  }
  saving.value = true
  try {
    const input = { name: name.value.trim(), body: payload }
    if (props.id) await updateTemplate(props.id, input)
    else await createTemplate(input)
    await router.push({ name: 'templates' })
  } catch (caught) {
    saveError.value = errorText(caught)
  } finally {
    saving.value = false
  }
}

async function readOnu(): Promise<void> {
  copyError.value = null
  copied.value = null
  if (!copy.oltId) {
    copyError.value = 'Elige la OLT.'
    return
  }
  try {
    const plan = await queryOlt(copy.oltId, {
      commands: ['onu.service_config'],
      pon: [],
      onu: [`${copy.pon}:${copy.onu}`],
    })
    await copyPlan.follow(plan.plan_id)
  } catch (caught) {
    copyError.value = errorText(caught)
  }
}

// Cuando vuelve la consulta: la configuración interpretada llena el formulario.
watch(copyPlan.plan, (plan) => {
  if (!plan || plan.status === 'queued') return
  const output = plan.result?.outputs?.find((item) => item.key === 'onu.service_config')
  if (!output?.ok) {
    copyError.value = output?.error ?? plan.result?.error ?? 'La OLT no devolvió la configuración.'
    return
  }
  if (output.parse_error || !output.data) {
    copyError.value = `No se reconoció la configuración de esa ONU: ${output.parse_error ?? 'sin datos'}`
    return
  }
  copied.value = output.data as OnuRunningConfig
  setBody(copied.value.template)
  if (!name.value) {
    const vlan = copied.value.template.services[0]?.vlan
    name.value = vlan ? `Plan VLAN ${vlan}` : `Copia ONU ${copy.pon}-${copy.onu}`
  }
})

function toggleWanService(service: (typeof WAN_SERVICES)[number]['value']): void {
  simple.wanAccess = simple.wanAccess.includes(service)
    ? simple.wanAccess.filter((item) => item !== service)
    : [...simple.wanAccess, service]
}

const opensWeb = computed(
  () => simple.management && simple.wanAccess.some((s) => s === 'http' || s === 'https'),
)

function toggleBind(port: string): void {
  simple.binds = simple.binds.includes(port)
    ? simple.binds.filter((item) => item !== port)
    : [...simple.binds, port]
}
</script>

<template>
  <PageHeader
    :title="id ? 'Editar plan' : 'Nuevo plan'"
    description="Lo que es igual para todos los clientes de un plan: perfiles, VLAN, si la ONU marca PPPoE y lleva WiFi, y su gestión remota. Lo de cada cliente (nombre, usuario y claves) se pide en cada alta."
    :back="{ name: 'templates' }"
    back-label="Planes"
  />

  <AlertBox v-if="loadError" tone="danger" class="mb-6">{{ loadError }}</AlertBox>

  <form class="space-y-6" novalidate @submit.prevent="save">
    <section class="card p-5 sm:p-6">
      <div class="flex items-center gap-2">
        <Copy class="size-4 text-muted" />
        <h2 class="font-semibold">Copiar de una ONU que ya funciona</h2>
      </div>
      <p class="mt-1 text-sm text-muted">
        Olterra lee su configuración en la OLT (solo lectura) y llena el formulario. No copia
        nombres, usuarios ni claves del cliente.
      </p>
      <div class="mt-4 grid gap-4 sm:grid-cols-4">
        <div class="sm:col-span-2">
          <label for="copy-olt" class="label">OLT</label>
          <select id="copy-olt" v-model="copy.oltId" class="input">
            <option value="">Elige una OLT</option>
            <option v-for="olt in olts.data.value ?? []" :key="olt.id" :value="olt.id">
              {{ olt.name }} ({{ olt.model ?? 'modelo sin indicar' }})
            </option>
          </select>
        </div>
        <div>
          <label for="copy-pon" class="label">PON</label>
          <input
            id="copy-pon"
            v-model.number="copy.pon"
            type="number"
            min="1"
            max="16"
            class="input"
          />
        </div>
        <div>
          <label for="copy-onu" class="label">ONU</label>
          <input
            id="copy-onu"
            v-model.number="copy.onu"
            type="number"
            min="1"
            max="128"
            class="input"
          />
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="btn-secondary"
          :disabled="copyPlan.waiting.value"
          @click="readOnu"
        >
          <LoaderCircle v-if="copyPlan.waiting.value" class="size-4 animate-spin" />
          <Copy v-else class="size-4" />
          Leer configuración
        </button>
        <span v-if="copyPlan.waiting.value" class="text-sm text-muted">
          Consultando la OLT{{ copyPlan.slow.value ? ' (está tardando)' : '' }}…
        </span>
      </div>
      <AlertBox v-if="copyError || copyPlan.error.value" tone="danger" class="mt-4">
        {{ copyError ?? copyPlan.error.value }}
      </AlertBox>
      <AlertBox v-if="copied" tone="success" title="Configuración copiada" class="mt-4">
        Se copió la ONU {{ copied.onu }} ({{ copied.client.description ?? 'sin descripción' }}).
        Revisa los campos y guarda.
        <template v-if="copied.ignored.length">
          Estas líneas no entran en el plan:
          <ul class="mt-2 list-disc space-y-0.5 pl-5 font-mono text-xs">
            <li v-for="line in copied.ignored" :key="line">{{ line }}</li>
          </ul>
        </template>
      </AlertBox>
    </section>

    <section class="card p-5 sm:p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="font-semibold">Plan</h2>
        <div class="flex gap-1">
          <button
            type="button"
            class="btn-ghost px-2.5 py-1 text-xs"
            :class="mode === 'simple' ? 'bg-subtle text-ink' : ''"
            @click="switchMode('simple')"
          >
            <SlidersHorizontal class="size-3.5" />
            Formulario
          </button>
          <button
            type="button"
            class="btn-ghost px-2.5 py-1 text-xs"
            :class="mode === 'json' ? 'bg-subtle text-ink' : ''"
            @click="switchMode('json')"
          >
            <Braces class="size-3.5" />
            JSON
          </button>
        </div>
      </div>

      <div class="mt-4">
        <label for="name" class="label">Nombre</label>
        <input
          id="name"
          v-model="name"
          class="input"
          maxlength="48"
          placeholder="Hogar 100M VLAN 111"
          :aria-invalid="!!show('name')"
        />
        <p v-if="show('name')" class="hint text-danger">{{ show('name') }}</p>
      </div>

      <div v-if="mode === 'simple'" class="mt-4 space-y-6">
        <div class="grid gap-4 sm:grid-cols-3">
          <div>
            <label for="vlan" class="label">VLAN</label>
            <input
              id="vlan"
              v-model.number="simple.vlan"
              type="number"
              min="1"
              max="4094"
              class="input"
              :aria-invalid="!!show('vlan')"
            />
            <p v-if="show('vlan')" class="hint text-danger">{{ show('vlan') }}</p>
            <p v-else class="hint">La del servicio, el service-port y la WAN.</p>
          </div>
          <div>
            <label for="dba" class="label">Perfil DBA</label>
            <input id="dba" v-model="simple.dba" class="input" :aria-invalid="!!show('dba')" />
            <p v-if="show('dba')" class="hint text-danger">{{ show('dba') }}</p>
            <p v-else class="hint">Ancho de banda de subida (T-CONT).</p>
          </div>
          <div>
            <label for="limit-down" class="label">
              Perfil de bajada <span class="text-muted">(opcional)</span>
            </label>
            <input id="limit-down" v-model="simple.limitDown" class="input" />
            <p v-if="show('limitDown')" class="hint text-danger">{{ show('limitDown') }}</p>
            <p v-else class="hint">traffic-limit downstream del GEM.</p>
          </div>
          <div>
            <label for="auth-profile" class="label">Perfil al autorizar</label>
            <input id="auth-profile" v-model="simple.authProfile" class="input" />
            <p v-if="show('authProfile')" class="hint text-danger">{{ show('authProfile') }}</p>
          </div>
          <div>
            <label for="onu-profile" class="label">
              Perfil de ONU <span class="text-muted">(opcional)</span>
            </label>
            <input id="onu-profile" v-model="simple.onuProfile" class="input" />
          </div>
          <div>
            <label for="cos" class="label">CoS</label>
            <input
              id="cos"
              v-model.number="simple.cos"
              type="number"
              min="0"
              max="7"
              class="input"
            />
          </div>
        </div>

        <fieldset class="rounded-lg border border-line p-4">
          <label class="flex items-center gap-2 font-medium">
            <input v-model="simple.pppoe" type="checkbox" class="size-4" />
            La ONU marca PPPoE (modo router)
          </label>
          <p class="mt-1 text-sm text-muted">
            En cada alta se piden el usuario y la clave PPPoE del cliente. Sin esto, la ONU queda en
            bridge y el PPPoE lo marca el router del cliente.
          </p>
          <div v-if="simple.pppoe" class="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <label for="mtu" class="label">MTU</label>
              <input
                id="mtu"
                v-model.number="simple.mtu"
                type="number"
                min="576"
                max="1500"
                class="input"
              />
              <p v-if="show('mtu')" class="hint text-danger">{{ show('mtu') }}</p>
            </div>
            <label class="flex items-center gap-2 self-end pb-2 text-sm">
              <input v-model="simple.nat" type="checkbox" class="size-4" />
              NAT
            </label>
            <div class="sm:col-span-3">
              <span class="label">Puertos que salen por esta WAN</span>
              <div class="flex flex-wrap gap-3">
                <label
                  v-for="port in UNI_PORTS"
                  :key="port"
                  class="flex items-center gap-1.5 font-mono text-sm"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="simple.binds.includes(port)"
                    @change="toggleBind(port)"
                  />
                  {{ port }}
                </label>
              </div>
              <p v-if="show('binds')" class="hint text-danger">{{ show('binds') }}</p>
            </div>
          </div>
        </fieldset>

        <fieldset class="rounded-lg border border-line p-4">
          <label class="flex items-center gap-2 font-medium">
            <input v-model="simple.wifi" type="checkbox" class="size-4" />
            Configurar WiFi
          </label>
          <p class="mt-1 text-sm text-muted">
            En cada alta se pueden poner el SSID y la clave (WPA2). Si se dejan vacíos, el WiFi de
            la ONU no se toca.
          </p>
        </fieldset>

        <fieldset class="rounded-lg border border-line p-4">
          <label class="flex items-center gap-2 font-medium">
            <input v-model="simple.management" type="checkbox" class="size-4" />
            <ShieldCheck class="size-4 text-muted" />
            Gestión remota de la ONU
          </label>
          <p class="mt-1 text-sm text-muted">
            Para entrar a la ONU del cliente desde internet. Desde la casa del cliente (LAN) todo
            sigue abierto; desde internet solo lo que marques.
          </p>
          <div v-if="simple.management" class="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <label for="firewall" class="label">Firewall de la ONU</label>
              <select id="firewall" v-model="simple.firewall" class="input">
                <option v-for="level in FIREWALL_LEVELS" :key="level.value" :value="level.value">
                  {{ level.label }}
                </option>
              </select>
            </div>
            <label class="flex items-center gap-2 self-end pb-2 text-sm sm:col-span-2">
              <input v-model="simple.pingWan" type="checkbox" class="size-4" />
              Responder ping desde internet
            </label>
            <div class="sm:col-span-3">
              <span class="label">Entrar a la ONU desde internet por</span>
              <div class="flex flex-wrap gap-4">
                <label
                  v-for="service in WAN_SERVICES"
                  :key="service.value"
                  class="flex items-center gap-1.5 text-sm"
                >
                  <input
                    type="checkbox"
                    class="size-4"
                    :checked="simple.wanAccess.includes(service.value)"
                    @change="toggleWanService(service.value)"
                  />
                  {{ service.label }}
                </label>
              </div>
              <p class="hint">Lo que no marques queda cerrado desde internet.</p>
            </div>
          </div>
          <AlertBox v-if="opensWeb" tone="warning" class="mt-4">
            Con la web de la ONU abierta a internet, cualquiera que adivine su usuario y contraseña
            de administración entra. No dejes la contraseña de fábrica.
          </AlertBox>
        </fieldset>
      </div>

      <div v-else class="mt-4">
        <label for="json" class="label">Cuerpo del plan</label>
        <textarea
          id="json"
          v-model="json"
          rows="18"
          spellcheck="false"
          class="input font-mono text-xs"
        />
        <p class="hint">
          Para varias VLAN, T-CONT o GEM. La API valida que cada GEM use un T-CONT que exista.
        </p>
      </div>
    </section>

    <AlertBox v-if="saveError" tone="danger">{{ saveError }}</AlertBox>
    <AlertBox v-else-if="submitted && Object.keys(problems).length" tone="warning">
      Revisa los campos marcados.
    </AlertBox>

    <div class="flex justify-end gap-2">
      <RouterLink :to="{ name: 'templates' }" class="btn-secondary">Cancelar</RouterLink>
      <button type="submit" class="btn-primary" :disabled="saving">
        <LoaderCircle v-if="saving" class="size-4 animate-spin" />
        <Save v-else class="size-4" />
        Guardar plan
      </button>
    </div>
  </form>
</template>
