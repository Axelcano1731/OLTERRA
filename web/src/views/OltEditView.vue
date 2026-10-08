<script setup lang="ts">
import { LoaderCircle, LockKeyhole, Save } from '@lucide/vue'
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { getOlt, updateOlt, type OltUpdate } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import { errorText, useAsync } from '@/lib/useAsync'

const props = defineProps<{ id: string }>()
const router = useRouter()
const olt = useAsync(() => getOlt(props.id))

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

const form = reactive({
  model: '',
  firmware: '',
  realIp: '',
  sshPort: 22,
  snmpPort: 161,
  // Las claves no vuelven de la API: vacío = no se cambia.
  username: '',
  password: '',
  enablePassword: '',
  snmpCommunity: '',
})

watch(olt.data, (value) => {
  if (!value) return
  form.model = value.model ?? ''
  form.firmware = value.firmware ?? ''
  form.realIp = value.real_ip ?? ''
  form.sshPort = value.ssh_port
  form.snmpPort = value.snmp_port
})

const submitted = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

const problems = computed(() => {
  const found: Partial<Record<keyof typeof form, string>> = {}
  const ip = form.realIp.trim()
  if (ip && !IPV4.test(ip)) found.realIp = 'Una IPv4, como 192.168.8.200.'
  else if (/^198\.1[89]\./.test(ip)) {
    found.realIp = 'Esa es una IP del túnel. Va la IP con la que abres la web de la OLT en tu red.'
  }
  for (const key of ['sshPort', 'snmpPort'] as const) {
    const port = form[key]
    if (!Number.isInteger(port) || port < 1 || port > 65535) found[key] = 'De 1 a 65535.'
  }
  return found
})

function show(field: keyof typeof form): string | undefined {
  return submitted.value ? problems.value[field] : undefined
}

/** Solo lo que cambió: la API no toca lo que no viene. */
function body(): OltUpdate {
  const current = olt.data.value
  const changes: OltUpdate = {}
  if (!current) return changes
  if (form.model.trim() !== (current.model ?? '')) changes.model = form.model.trim()
  if (form.firmware.trim() !== (current.firmware ?? '')) changes.firmware = form.firmware.trim()
  if (form.realIp.trim() && form.realIp.trim() !== current.real_ip) {
    changes.real_ip = form.realIp.trim()
  }
  if (form.sshPort !== current.ssh_port) changes.ssh_port = form.sshPort
  if (form.snmpPort !== current.snmp_port) changes.snmp_port = form.snmpPort
  if (form.username.trim()) changes.username = form.username.trim()
  if (form.password) changes.password = form.password
  if (form.enablePassword) changes.enable_password = form.enablePassword
  if (form.snmpCommunity) changes.snmp_community = form.snmpCommunity
  return changes
}

async function submit(): Promise<void> {
  submitted.value = true
  error.value = null
  if (Object.keys(problems.value).length > 0) return
  const changes = body()
  if (Object.keys(changes).length === 0) {
    error.value = 'No cambiaste nada.'
    return
  }
  busy.value = true
  try {
    await updateOlt(props.id, changes)
    await router.push({ name: 'olt', params: { id: props.id }, query: { editada: '1' } })
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <LoadingBlock v-if="olt.loading.value && !olt.data.value" />
  <AlertBox v-else-if="olt.error.value" tone="danger">{{ olt.error.value }}</AlertBox>

  <template v-else-if="olt.data.value">
    <PageHeader
      :title="`Editar ${olt.data.value.name}`"
      description="Solo se guarda lo que cambies. Las claves se vuelven a cifrar con la llave de tu ISP y no se muestran: deja vacío lo que no quieras cambiar."
      :back="{ name: 'olt', params: { id } }"
      :back-label="olt.data.value.name"
    />

    <form class="space-y-6" novalidate @submit.prevent="submit">
      <section class="card p-5 sm:p-6">
        <h2 class="font-semibold">Equipo y conexión</h2>
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <label for="model" class="label">Modelo</label>
            <input id="model" v-model="form.model" class="input" placeholder="V1600G0-B" />
            <p class="hint">Como sale en la web de la OLT (Device Model).</p>
          </div>
          <div>
            <label for="firmware" class="label">Firmware</label>
            <input id="firmware" v-model="form.firmware" class="input" placeholder="V1.4.8R" />
          </div>
          <div>
            <label for="real-ip" class="label">IP de la OLT en tu red</label>
            <input
              id="real-ip"
              v-model="form.realIp"
              class="input font-mono"
              inputmode="decimal"
              :aria-invalid="!!show('realIp')"
            />
            <p v-if="show('realIp')" class="hint text-danger">{{ show('realIp') }}</p>
            <p v-else-if="olt.data.value.router_id" class="hint">
              Si la cambias, rota las llaves de su MikroTik en Túnel para publicar la nueva.
            </p>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label for="ssh-port" class="label">Puerto SSH</label>
              <input
                id="ssh-port"
                v-model.number="form.sshPort"
                type="number"
                min="1"
                max="65535"
                class="input"
              />
              <p v-if="show('sshPort')" class="hint text-danger">{{ show('sshPort') }}</p>
            </div>
            <div>
              <label for="snmp-port" class="label">Puerto SNMP</label>
              <input
                id="snmp-port"
                v-model.number="form.snmpPort"
                type="number"
                min="1"
                max="65535"
                class="input"
              />
              <p v-if="show('snmpPort')" class="hint text-danger">{{ show('snmpPort') }}</p>
            </div>
          </div>
        </div>
      </section>

      <section class="card p-5 sm:p-6">
        <div class="flex items-center gap-2">
          <LockKeyhole class="size-4 text-muted" />
          <h2 class="font-semibold">Credenciales</h2>
        </div>
        <p class="mt-1 text-sm text-muted">
          Son las de la CLI (SSH) de la OLT, que pueden ser distintas de las de su web. Vacío = no
          cambia.
        </p>
        <div class="mt-4 grid gap-4 sm:grid-cols-2">
          <div>
            <label for="username" class="label">Usuario</label>
            <input
              id="username"
              v-model="form.username"
              class="input"
              autocomplete="off"
              placeholder="Sin cambio"
            />
          </div>
          <div>
            <label for="password" class="label">Clave</label>
            <input
              id="password"
              v-model="form.password"
              type="password"
              class="input"
              autocomplete="new-password"
              placeholder="Sin cambio"
            />
          </div>
          <div>
            <label for="enable" class="label">Clave de enable</label>
            <input
              id="enable"
              v-model="form.enablePassword"
              type="password"
              class="input"
              autocomplete="new-password"
              placeholder="Sin cambio"
            />
            <p class="hint">La que pide la OLT al escribir enable.</p>
          </div>
          <div>
            <label for="community" class="label">Comunidad SNMP</label>
            <input
              id="community"
              v-model="form.snmpCommunity"
              type="password"
              class="input"
              autocomplete="new-password"
              placeholder="Sin cambio"
            />
          </div>
        </div>
      </section>

      <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>

      <div class="flex justify-end gap-2">
        <RouterLink :to="{ name: 'olt', params: { id } }" class="btn-secondary"
          >Cancelar</RouterLink
        >
        <button type="submit" class="btn-primary" :disabled="busy">
          <LoaderCircle v-if="busy" class="size-4 animate-spin" />
          <Save v-else class="size-4" />
          Guardar cambios
        </button>
      </div>
    </form>
  </template>
</template>
