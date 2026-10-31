<script setup lang="ts">
import { Eye, EyeOff, Globe, LoaderCircle, RefreshCw, Wifi } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'

import {
  changeOnuPppoe,
  changeOnuWifi,
  type OnuStatus,
  type PppoeWan,
  type WanStatus,
  type WifiStatus,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import StatusPill from '@/components/StatusPill.vue'
import { oltBrand } from '@/lib/labels'
import { useOnuRead } from '@/lib/useOnuRead'
import { useWrite } from '@/lib/useWrite'

const props = defineProps<{ status: OnuStatus; driver: string }>()

const vsol = computed(() => props.driver === 'vsol-gpon')
const reader = useOnuRead()
const COMMANDS = ['onu.wan_status', 'onu.wifi_status', 'onu.wan_config']

function load(): void {
  if (vsol.value && props.status.online) {
    void reader.run(props.status.olt_id, props.status.pon, props.status.onu, COMMANDS)
  }
}
onMounted(load)

const wan = computed(() => reader.read<WanStatus[]>('onu.wan_status'))
const wifi = computed(() => reader.read<WifiStatus>('onu.wifi_status'))
const saved = computed(() => reader.read<PppoeWan[]>('onu.wan_config'))

// La WAN PPPoE de la ONU y la línea con que la OLT la guarda (para reescribirla).
const pppoe = computed(() => wan.value.data?.find((item) => item.connection === 'PPPOE') ?? null)
const savedWan = computed(
  () =>
    saved.value.data?.find((item) => !pppoe.value || item.index === pppoe.value.index) ??
    saved.value.data?.[0] ??
    null,
)

/** "Unsupport private protocol": la ONU no habla los comandos privados de VSOL. */
const unsupported = computed(() =>
  [wan.value.error, wifi.value.error].some((text) => /unsupport/i.test(text ?? '')),
)

// --- Cambios ------------------------------------------------------------------------------
const write = useWrite({
  pppoe: {
    busy: 'Cambiando el PPPoE en la OLT…',
    ok: 'Listo: la OLT guardó el PPPoE nuevo y se lo pasó a la ONU. Se reconecta en unos segundos.',
  },
  wifi: {
    busy: 'Cambiando el Wi‑Fi en la OLT…',
    ok: 'Listo: la OLT guardó el Wi‑Fi nuevo y se lo pasó a la ONU. Los equipos se reconectan con la clave nueva.',
  },
})
type Form = 'pppoe' | 'wifi' | null
const form = ref<Form>(null)
const showSecret = ref(false)
const pppoeForm = ref({ user: '', password: '' })
const wifiForm = ref({ ssid: '', key: '', hidden: false })
const formError = ref<string | null>(null)

function openForm(next: Exclude<Form, null>): void {
  write.reset()
  formError.value = null
  showSecret.value = false
  if (next === 'pppoe') {
    pppoeForm.value = {
      user: savedWan.value?.pppoe_user ?? pppoe.value?.pppoe_user ?? '',
      password: '',
    }
  } else {
    wifiForm.value = {
      ssid: wifi.value.data?.name ?? '',
      key: '',
      hidden: !!wifi.value.data?.hidden,
    }
  }
  form.value = next
}

const SSID = /^[A-Za-z0-9_.-]{1,32}$/
const USER = /^[A-Za-z0-9_.@-]{1,64}$/
const SECRET = /^[!-~]+$/

async function submit(): Promise<void> {
  formError.value = null
  const where = { pon: props.status.pon, onu: props.status.onu }
  if (form.value === 'pppoe') {
    const { user, password } = pppoeForm.value
    const target = savedWan.value
    if (!target) return
    if (!USER.test(user.trim())) {
      formError.value = 'El usuario PPPoE va sin espacios (letras, números, punto, guion, @).'
      return
    }
    if (!password || !SECRET.test(password) || password.includes('?') || password.length > 63) {
      formError.value = 'La clave PPPoE va sin espacios ni signo de pregunta (hasta 63 caracteres).'
      return
    }
    const ok = await write.run('pppoe', () =>
      changeOnuPppoe(props.status.olt_id, {
        ...where,
        wan: { kind: target.kind, index: target.index, route: target.route, tail: target.tail },
        pppoe_user: user.trim(),
        pppoe_password: password,
      }),
    )
    pppoeForm.value.password = ''
    if (ok) afterChange()
  } else if (form.value === 'wifi') {
    const { ssid, key, hidden } = wifiForm.value
    if (!SSID.test(ssid.trim())) {
      formError.value =
        'El nombre de la red va sin espacios ni tildes (la OLT no los acepta): usa _ o - en su lugar.'
      return
    }
    if (key.length < 8 || key.length > 63 || !SECRET.test(key) || key.includes('?')) {
      formError.value =
        'La clave del Wi‑Fi: de 8 a 63 caracteres, sin espacios ni signo de pregunta.'
      return
    }
    const ok = await write.run('wifi', () =>
      changeOnuWifi(props.status.olt_id, { ...where, ssid: ssid.trim(), wifi_key: key, hidden }),
    )
    wifiForm.value.key = ''
    if (ok) afterChange()
  }
}

let reloadTimer: number | undefined
function afterChange(): void {
  form.value = null
  // La ONU tarda unos segundos en aplicar lo nuevo: se vuelve a leer para mostrarlo.
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(load, 8000)
}
watch(
  () => props.status.online,
  (online, before) => {
    if (online && !before) load()
  },
)

function yesNo(value: boolean | null | undefined, yes: string, no: string): string {
  return value === null || value === undefined ? '—' : value ? yes : no
}
</script>

<template>
  <AlertBox v-if="!vsol" tone="info" :title="`Wi‑Fi y PPPoE en la OLT ${oltBrand(driver)}`">
    <template v-if="driver === 'bcd-gpon'">
      Este firmware de la BCD no configura el Wi‑Fi ni el PPPoE de la ONU desde la OLT: se cambian
      en la propia ONU (su página web o TR‑069).
    </template>
    <template v-else>
      La HSGQ solo cambia el Wi‑Fi y el PPPoE de las ONU que configuró la propia OLT, y las de esta
      red se configuraron en la ONU (la OLT no las ve). Se habilita cuando lo probemos con una ONU
      de prueba; mientras tanto se cambian en la propia ONU.
    </template>
  </AlertBox>

  <AlertBox v-else-if="!status.online" tone="warning" title="La ONU está caída">
    La OLT solo puede leer y cambiar el Wi‑Fi y el PPPoE con la ONU en línea.
  </AlertBox>

  <div v-else class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-sm text-muted">
        Lo que reporta la ONU ahora. Las claves no se muestran: la OLT las guarda y Olterra no.
      </p>
      <button
        type="button"
        class="btn-ghost px-2.5 py-1 text-xs"
        :disabled="reader.waiting.value"
        @click="load"
      >
        <RefreshCw :class="['size-3.5', reader.waiting.value ? 'animate-spin' : '']" />
        Leer de nuevo
      </button>
    </div>

    <AlertBox v-if="reader.error.value" tone="danger">{{ reader.error.value }}</AlertBox>
    <p v-else-if="reader.waiting.value && !reader.done.value" class="card p-5 text-sm text-muted">
      <LoaderCircle class="mr-1 inline size-4 animate-spin" />
      Preguntando a la ONU por la OLT{{ reader.slow.value ? ' (está tardando)' : '' }}…
    </p>
    <AlertBox v-else-if="unsupported" tone="info" title="Esta ONU no deja hacerlo desde la OLT">
      No habla los comandos privados de VSOL (no es una ONU VSOL o su firmware no los tiene): su
      Wi‑Fi y su PPPoE se cambian en la propia ONU.
    </AlertBox>

    <div v-else-if="reader.done.value" class="grid gap-4 lg:grid-cols-2">
      <!-- Internet (PPPoE) -->
      <section class="card p-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="flex items-center gap-2 font-semibold">
            <Globe class="size-4 text-muted" /> Internet (PPPoE)
          </h3>
          <StatusPill
            v-if="pppoe"
            :tone="pppoe.status?.toLowerCase() === 'connected' ? 'success' : 'warning'"
          >
            {{ pppoe.status?.toLowerCase() === 'connected' ? 'Conectado' : (pppoe.status ?? '—') }}
          </StatusPill>
        </div>
        <p v-if="wan.error" class="text-sm text-danger">{{ wan.error }}</p>
        <p v-else-if="!pppoe" class="text-sm text-muted">
          La ONU no tiene una WAN PPPoE (puede estar en modo puente o con IP fija).
        </p>
        <dl v-else class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt class="text-muted">Usuario</dt>
          <dd class="font-mono">{{ pppoe.pppoe_user ?? '—' }}</dd>
          <dt class="text-muted">Clave</dt>
          <dd class="text-muted">no se muestra</dd>
          <dt class="text-muted">VLAN</dt>
          <dd>{{ pppoe.vlan ?? '—' }}</dd>
          <template v-if="pppoe.ip">
            <dt class="text-muted">IP</dt>
            <dd class="font-mono">{{ pppoe.ip }}</dd>
          </template>
          <dt class="text-muted">MTU</dt>
          <dd>{{ pppoe.mtu ?? '—' }}</dd>
        </dl>
        <template v-if="pppoe && form !== 'pppoe'">
          <button
            v-if="savedWan"
            type="button"
            class="btn-secondary mt-4"
            :disabled="write.waiting.value"
            @click="openForm('pppoe')"
          >
            Cambiar usuario o clave
          </button>
          <p v-else class="mt-4 text-xs text-muted">
            Esta WAN no está en la configuración de la OLT (se configuró en la ONU): se cambia en la
            propia ONU.
          </p>
        </template>
        <form v-if="form === 'pppoe'" class="mt-4 space-y-3" novalidate @submit.prevent="submit">
          <div>
            <label for="pppoe-usuario" class="label">Usuario PPPoE</label>
            <input
              id="pppoe-usuario"
              v-model="pppoeForm.user"
              class="input font-mono"
              autocomplete="off"
              spellcheck="false"
            />
          </div>
          <div>
            <label for="pppoe-clave" class="label">Clave PPPoE</label>
            <div class="relative">
              <input
                id="pppoe-clave"
                v-model="pppoeForm.password"
                :type="showSecret ? 'text' : 'password'"
                class="input pr-10 font-mono"
                autocomplete="new-password"
              />
              <button
                type="button"
                class="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
                :aria-label="showSecret ? 'Ocultar clave' : 'Mostrar clave'"
                @click="showSecret = !showSecret"
              >
                <EyeOff v-if="showSecret" class="size-4" />
                <Eye v-else class="size-4" />
              </button>
            </div>
            <p class="hint">
              Que coincida con el del MikroTik. La ONU se reconecta con la nueva; la VLAN no cambia.
            </p>
          </div>
          <AlertBox v-if="formError" tone="danger">{{ formError }}</AlertBox>
          <div class="flex justify-end gap-2">
            <button type="button" class="btn-ghost" @click="form = null">Cancelar</button>
            <button type="submit" class="btn-primary" :disabled="write.waiting.value">
              <LoaderCircle v-if="write.waiting.value" class="size-4 animate-spin" />
              Cambiar PPPoE
            </button>
          </div>
        </form>
      </section>

      <!-- Wi‑Fi -->
      <section class="card p-5">
        <div class="mb-3 flex items-center justify-between gap-2">
          <h3 class="flex items-center gap-2 font-semibold">
            <Wifi class="size-4 text-muted" /> Wi‑Fi
          </h3>
          <StatusPill v-if="wifi.data" :tone="wifi.data.enabled ? 'success' : 'neutral'">
            {{ yesNo(wifi.data.enabled, 'Encendido', 'Apagado') }}
          </StatusPill>
        </div>
        <p v-if="wifi.error" class="text-sm text-danger">{{ wifi.error }}</p>
        <dl v-else-if="wifi.data" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt class="text-muted">Nombre de la red</dt>
          <dd class="font-medium">{{ wifi.data.name ?? '—' }}</dd>
          <dt class="text-muted">Clave</dt>
          <dd class="text-muted">no se muestra</dd>
          <dt class="text-muted">Seguridad</dt>
          <dd>{{ [wifi.data.auth, wifi.data.encrypt].filter(Boolean).join(' · ') || '—' }}</dd>
          <dt class="text-muted">Red oculta</dt>
          <dd>{{ yesNo(wifi.data.hidden, 'Sí', 'No') }}</dd>
        </dl>
        <button
          v-if="wifi.data && form !== 'wifi'"
          type="button"
          class="btn-secondary mt-4"
          :disabled="write.waiting.value"
          @click="openForm('wifi')"
        >
          Cambiar nombre o clave
        </button>
        <form v-if="form === 'wifi'" class="mt-4 space-y-3" novalidate @submit.prevent="submit">
          <div>
            <label for="wifi-nombre" class="label">Nombre de la red</label>
            <input
              id="wifi-nombre"
              v-model="wifiForm.ssid"
              class="input"
              maxlength="32"
              autocomplete="off"
              spellcheck="false"
            />
            <p class="hint">Sin espacios ni tildes: la OLT no los acepta (usa _ o -).</p>
          </div>
          <div>
            <label for="wifi-clave" class="label">Clave nueva</label>
            <div class="relative">
              <input
                id="wifi-clave"
                v-model="wifiForm.key"
                :type="showSecret ? 'text' : 'password'"
                class="input pr-10 font-mono"
                autocomplete="new-password"
                minlength="8"
                maxlength="63"
              />
              <button
                type="button"
                class="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted hover:text-ink"
                :aria-label="showSecret ? 'Ocultar clave' : 'Mostrar clave'"
                @click="showSecret = !showSecret"
              >
                <EyeOff v-if="showSecret" class="size-4" />
                <Eye v-else class="size-4" />
              </button>
            </div>
            <p class="hint">De 8 a 63 caracteres (WPA2). Díctasela al cliente antes de guardar.</p>
          </div>
          <label class="flex items-center gap-2 text-sm">
            <input v-model="wifiForm.hidden" type="checkbox" />
            Red oculta (no aparece en la lista de redes)
          </label>
          <AlertBox v-if="formError" tone="danger">{{ formError }}</AlertBox>
          <div class="flex justify-end gap-2">
            <button type="button" class="btn-ghost" @click="form = null">Cancelar</button>
            <button type="submit" class="btn-primary" :disabled="write.waiting.value">
              <LoaderCircle v-if="write.waiting.value" class="size-4 animate-spin" />
              Cambiar Wi‑Fi
            </button>
          </div>
        </form>
      </section>
    </div>

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
  </div>
</template>
