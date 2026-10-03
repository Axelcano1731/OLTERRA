<script setup lang="ts">
import { LoaderCircle, LockKeyhole, Save } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { createOlt, getOltDefaults, listRouters, type OltCreate } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import PageHeader from '@/components/PageHeader.vue'
import { errorText, useAsync } from '@/lib/useAsync'

// Sugerencias escritas como las muestra la web de la OLT; se puede escribir cualquier otro. El
// driver compara sin guiones, así que V1600G1-B y V1600G1B son el mismo (drivers/base.py).
const KNOWN_MODELS = ['V1600G0-B', 'V1600G1', 'V1600G1-B', 'V1600G2', 'V1600GS']

const NAME = /^[A-Za-z0-9_.-]{1,32}$/
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/

const router = useRouter()
const routers = useAsync(listRouters)
const defaults = useAsync(getOltDefaults)
const factory = computed(() => defaults.data.value?.password_configured === true)

const form = reactive({
  name: '',
  model: '',
  firmware: '',
  routerId: '',
  realIp: '',
  sshPort: 22,
  snmpPort: 161,
  username: '',
  password: '',
  enablePassword: '',
  snmpCommunity: '',
  latitude: '',
  longitude: '',
})

const submitted = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

function port(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 65535
}

function coordinate(text: string, limit: number): boolean {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) && Math.abs(value) <= limit
}

const problems = computed(() => {
  const found: Partial<Record<keyof typeof form, string>> = {}
  if (!NAME.test(form.name)) {
    found.name = 'Hasta 32 caracteres: letras, números, punto, guion o guion bajo.'
  }
  if (!IPV4.test(form.realIp.trim())) found.realIp = 'Una IPv4, como 192.168.8.200.'
  // 198.18.x y 198.19.x son del túnel: la del router o una IP NAT, nunca la de la OLT.
  else if (/^198\.1[89]\./.test(form.realIp.trim())) {
    found.realIp = 'Esa es una IP del túnel. Va la IP con la que abres la web de la OLT en tu red.'
  }
  if (!port(form.sshPort)) found.sshPort = 'De 1 a 65535.'
  if (!port(form.snmpPort)) found.snmpPort = 'De 1 a 65535.'
  // Con clave de fábrica en el servidor se puede dejar vacío (OLT nueva); sin ella, es obligatoria.
  if (!form.password && !factory.value) found.password = 'Falta la clave.'
  if (form.password && !form.username.trim()) found.username = 'Falta el usuario.'
  const hasLat = form.latitude.trim() !== ''
  const hasLon = form.longitude.trim() !== ''
  if (hasLat || hasLon) {
    if (!coordinate(form.latitude, 90)) found.latitude = 'Entre -90 y 90.'
    if (!coordinate(form.longitude, 180)) found.longitude = 'Entre -180 y 180.'
  }
  return found
})

const invalid = computed(() => Object.keys(problems.value).length > 0)

function show(field: keyof typeof form): string | undefined {
  return submitted.value ? problems.value[field] : undefined
}

function body(): OltCreate {
  const optional = (text: string) => text.trim() || undefined
  return {
    name: form.name.trim(),
    model: optional(form.model),
    firmware: optional(form.firmware),
    router_id: form.routerId || undefined,
    real_ip: form.realIp.trim(),
    ssh_port: form.sshPort,
    snmp_port: form.snmpPort,
    username: optional(form.username),
    password: form.password || undefined,
    enable_password: form.enablePassword || undefined,
    snmp_community: form.snmpCommunity || undefined,
    latitude: form.latitude.trim() ? Number(form.latitude) : undefined,
    longitude: form.longitude.trim() ? Number(form.longitude) : undefined,
  }
}

async function submit(): Promise<void> {
  submitted.value = true
  error.value = null
  if (invalid.value) return
  busy.value = true
  try {
    const olt = await createOlt(body())
    await router.push({
      name: 'olt',
      params: { id: olt.id },
      query: { nueva: '1', ...(olt.used_default_credentials ? { fabrica: '1' } : {}) },
    })
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader
    title="Agregar OLT"
    description="Olterra entra a la OLT por SSH y SNMP solo para leer. Las credenciales se cifran con la llave de tu ISP y no se vuelven a mostrar."
    :back="{ name: 'olts' }"
    back-label="OLT"
  />

  <form class="space-y-6" novalidate @submit.prevent="submit">
    <section class="card p-5 sm:p-6">
      <h2 class="font-semibold">Equipo</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-3">
        <div>
          <label for="name" class="label">Nombre</label>
          <input
            id="name"
            v-model="form.name"
            class="input"
            placeholder="OLT-CENTRO"
            maxlength="32"
            :aria-invalid="!!show('name')"
          />
          <p v-if="show('name')" class="hint text-danger">{{ show('name') }}</p>
        </div>
        <div>
          <label for="model" class="label">Modelo</label>
          <input
            id="model"
            v-model="form.model"
            class="input"
            list="models"
            placeholder="V1600G0-B"
          />
          <datalist id="models">
            <option v-for="model in KNOWN_MODELS" :key="model" :value="model" />
          </datalist>
          <p class="hint">
            Escríbelo como sale en la web de la OLT (Device Model); puede ser cualquiera.
          </p>
        </div>
        <div>
          <label for="firmware" class="label">Firmware</label>
          <input id="firmware" v-model="form.firmware" class="input" placeholder="V1.4.8R" />
          <p class="hint">Software Version en la web de la OLT.</p>
        </div>
      </div>
    </section>

    <section class="card p-5 sm:p-6">
      <h2 class="font-semibold">Cómo llega Olterra</h2>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label for="router" class="label">Detrás de</label>
          <select id="router" v-model="form.routerId" class="input">
            <option value="">Conexión directa (sin túnel)</option>
            <option v-for="item in routers.data.value ?? []" :key="item.id" :value="item.id">
              MikroTik {{ item.name }} (túnel {{ item.overlay_ip }})
            </option>
          </select>
          <p class="hint">
            Con túnel, la OLT recibe una IP única y el script del MikroTik la publica (NAT 1:1).
          </p>
        </div>
        <div>
          <label for="real-ip" class="label">IP de la OLT en tu red</label>
          <input
            id="real-ip"
            v-model="form.realIp"
            class="input font-mono"
            placeholder="192.168.8.200"
            inputmode="decimal"
            :aria-invalid="!!show('realIp')"
          />
          <p v-if="show('realIp')" class="hint text-danger">{{ show('realIp') }}</p>
          <p v-else class="hint">
            La IP con la que abres la web de la OLT desde tu red. No es la del túnel.
          </p>
        </div>
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
    </section>

    <section class="card p-5 sm:p-6">
      <div class="flex items-center gap-2">
        <LockKeyhole class="size-4 text-muted" />
        <h2 class="font-semibold">Credenciales</h2>
      </div>
      <p class="mt-1 text-sm text-muted">
        <template v-if="factory">
          Si la OLT es nueva (nunca se ha entrado por SSH), deja usuario y clave vacíos: Olterra usa
          los de fábrica ({{ defaults.data.value?.username }}). Si ya la configuraste, escribe los
          tuyos.
        </template>
        <template v-else>
          Usa un usuario propio para Olterra, no el de fábrica: la clave de fábrica está en el
          manual público.
        </template>
      </p>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label for="username" class="label">
            Usuario <span v-if="factory" class="text-muted">(opcional)</span>
          </label>
          <input
            id="username"
            v-model="form.username"
            class="input"
            autocomplete="off"
            :placeholder="factory ? defaults.data.value?.username : ''"
            :aria-invalid="!!show('username')"
          />
          <p v-if="show('username')" class="hint text-danger">{{ show('username') }}</p>
        </div>
        <div>
          <label for="password" class="label">
            Clave <span v-if="factory" class="text-muted">(vacía = la de fábrica)</span>
          </label>
          <input
            id="password"
            v-model="form.password"
            type="password"
            class="input"
            autocomplete="off"
            :aria-invalid="!!show('password')"
          />
          <p v-if="show('password')" class="hint text-danger">{{ show('password') }}</p>
        </div>
        <div>
          <label for="enable" class="label"
            >Clave de enable <span class="text-muted">(la que configuró el cliente)</span></label
          >
          <input
            id="enable"
            v-model="form.enablePassword"
            type="password"
            class="input"
            autocomplete="off"
          />
          <p class="hint">
            No hay una de fábrica que valga: es la que el cliente puso en su OLT. Sin ella no se
            pueden correr comandos de modo privilegiado.
          </p>
        </div>
        <div>
          <label for="community" class="label">
            Comunidad SNMP <span class="text-muted">(opcional)</span>
          </label>
          <input
            id="community"
            v-model="form.snmpCommunity"
            type="password"
            class="input"
            autocomplete="off"
          />
        </div>
      </div>
    </section>

    <section class="card p-5 sm:p-6">
      <h2 class="font-semibold">
        Ubicación <span class="text-sm font-normal text-muted">(opcional)</span>
      </h2>
      <p class="mt-1 text-sm text-muted">Para ubicarla en el mapa FTTH cuando llegue.</p>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label for="lat" class="label">Latitud</label>
          <input
            id="lat"
            v-model="form.latitude"
            class="input font-mono"
            placeholder="4.4389"
            inputmode="decimal"
          />
          <p v-if="show('latitude')" class="hint text-danger">{{ show('latitude') }}</p>
        </div>
        <div>
          <label for="lon" class="label">Longitud</label>
          <input
            id="lon"
            v-model="form.longitude"
            class="input font-mono"
            placeholder="-75.2322"
            inputmode="decimal"
          />
          <p v-if="show('longitude')" class="hint text-danger">{{ show('longitude') }}</p>
        </div>
      </div>
    </section>

    <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>
    <AlertBox v-else-if="submitted && invalid" tone="warning">Revisa los campos marcados.</AlertBox>

    <div class="flex justify-end gap-2">
      <RouterLink :to="{ name: 'olts' }" class="btn-secondary">Cancelar</RouterLink>
      <button type="submit" class="btn-primary" :disabled="busy">
        <LoaderCircle v-if="busy" class="size-4 animate-spin" />
        <Save v-else class="size-4" />
        Guardar OLT
      </button>
    </div>
  </form>
</template>
