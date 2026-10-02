<script setup lang="ts">
import { KeyRound, LoaderCircle, Plus, RotateCw, Waypoints, X } from '@lucide/vue'
import { computed, nextTick, ref, shallowRef, useTemplateRef } from 'vue'

import {
  createRouter,
  listRouters,
  rotateRouter,
  type RouterScripts,
  type TunnelRouter,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import CopyBlock from '@/components/CopyBlock.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { errorText, useAsync } from '@/lib/useAsync'
import { can } from '@/session'

const NAME = /^[A-Za-z0-9_.-]{1,32}$/

// RouterOS 7 va por WireGuard; RouterOS 6 no lo tiene y va por SSTP (TLS sobre TCP).
type Major = '7' | '6'
const VERSIONS: { value: Major; label: string }[] = [
  { value: '7', label: '7.x · WireGuard' },
  { value: '6', label: '6.x · SSTP' },
]

const routers = useAsync(listRouters)
const canWrite = computed(() => can('tunnel:write'))

const showForm = ref(false)
const name = ref('')
const version = ref<Major>('7')
const busy = ref(false)
const formError = ref<string | null>(null)

const confirming = ref<string | null>(null)
const rotateVersion = ref<Major>('7')
const rotating = ref<string | null>(null)
const rowError = ref<string | null>(null)

// Los scripts traen la credencial del router: viven solo en memoria hasta que se cierran.
const scripts = shallowRef<RouterScripts | null>(null)
const scriptsReason = ref<'created' | 'rotated'>('created')
const scriptsBox = useTemplateRef<HTMLElement>('scripts')

const isSstp = computed(() => scripts.value?.router.transport === 'sstp')

function major(router: TunnelRouter): Major {
  return router.transport === 'sstp' ? '6' : '7'
}

async function reveal(result: RouterScripts, reason: 'created' | 'rotated'): Promise<void> {
  scripts.value = result
  scriptsReason.value = reason
  await nextTick()
  scriptsBox.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function create(): Promise<void> {
  formError.value = null
  if (!NAME.test(name.value.trim())) {
    formError.value = 'El nombre va sin espacios: letras, números, punto, guion o guion bajo.'
    return
  }
  busy.value = true
  try {
    const result = await createRouter(name.value.trim(), version.value)
    name.value = ''
    showForm.value = false
    await routers.reload()
    await reveal(result, 'created')
  } catch (caught) {
    formError.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

function askRotation(router: TunnelRouter): void {
  confirming.value = router.id
  rotateVersion.value = major(router)
}

async function rotate(router: TunnelRouter): Promise<void> {
  rowError.value = null
  rotating.value = router.id
  try {
    // Solo si cambia la versión se manda: así se pasa un router v6 dado de alta como v7 a SSTP.
    const changed = rotateVersion.value !== major(router) ? rotateVersion.value : undefined
    const result = await rotateRouter(router.id, changed)
    confirming.value = null
    await routers.reload()
    await reveal(result, 'rotated')
  } catch (caught) {
    rowError.value = errorText(caught)
  } finally {
    rotating.value = null
  }
}

function closeScripts(): void {
  scripts.value = null
}
</script>

<template>
  <PageHeader
    title="Túnel"
    description="Olterra llega a tus OLT por un túnel que sale de tu MikroTik hacia el concentrador: WireGuard en RouterOS 7, SSTP en RouterOS 6. No abres puertos: corres un script en el MikroTik."
  >
    <template #actions>
      <button
        v-if="canWrite && !showForm"
        type="button"
        class="btn-primary"
        @click="showForm = true"
      >
        <Plus class="size-4" />
        Agregar MikroTik
      </button>
    </template>
  </PageHeader>

  <section v-if="showForm" class="card mb-6 p-5 sm:p-6" aria-labelledby="nuevo-router">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h2 id="nuevo-router" class="font-semibold">Agregar un MikroTik</h2>
        <p class="mt-1 text-sm text-muted">
          Olterra genera su credencial y el script. La credencial va en el script y no se guarda.
        </p>
      </div>
      <button type="button" class="btn-ghost p-1.5" aria-label="Cerrar" @click="showForm = false">
        <X class="size-4" />
      </button>
    </div>
    <form
      class="mt-4 grid gap-4 sm:grid-cols-[1fr_13rem_auto] sm:items-end"
      @submit.prevent="create"
    >
      <div>
        <label for="router-name" class="label">Nombre</label>
        <input
          id="router-name"
          v-model="name"
          class="input"
          placeholder="BNG-CENTRO"
          maxlength="32"
        />
      </div>
      <div>
        <label for="routeros" class="label">RouterOS</label>
        <select id="routeros" v-model="version" class="input">
          <option v-for="item in VERSIONS" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>
      </div>
      <button type="submit" class="btn-primary" :disabled="busy">
        <LoaderCircle v-if="busy" class="size-4 animate-spin" />
        <KeyRound v-else class="size-4" />
        Generar script
      </button>
    </form>
    <AlertBox v-if="version === '6'" tone="info" class="mt-4" title="RouterOS 6 va por SSTP">
      No tiene WireGuard: el túnel va cifrado con TLS por TCP. Convive con el L2TP de ISPWatch en el
      mismo router.
    </AlertBox>
    <AlertBox v-if="formError" tone="danger" class="mt-4">{{ formError }}</AlertBox>
  </section>

  <section
    v-if="scripts"
    ref="scripts"
    class="card mb-6 scroll-mt-20 border-accent/50 p-5 sm:p-6"
    aria-labelledby="scripts-titulo"
  >
    <div class="flex items-start justify-between gap-3">
      <div>
        <h2 id="scripts-titulo" class="font-semibold">
          {{ scriptsReason === 'created' ? 'Script listo' : 'Credencial rotada' }} para
          {{ scripts.router.name }}
        </h2>
        <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusPill tone="accent">{{ isSstp ? 'SSTP · RouterOS 6' : 'WireGuard' }}</StatusPill>
          IP en el túnel <span class="font-mono">{{ scripts.router.overlay_ip }}</span>
        </p>
      </div>
      <button type="button" class="btn-secondary" @click="closeScripts">Ya lo guardé</button>
    </div>
    <AlertBox tone="warning" class="mt-4" title="Se muestra una sola vez">
      El script trae {{ isSstp ? 'la clave SSTP' : 'la llave privada' }} del router y Olterra no la
      guarda. Descárgalo ahora; si lo pierdes, rota la credencial y corre el script nuevo.
      <template v-if="scriptsReason === 'rotated'">
        El túnel con la credencial anterior deja de funcionar hasta que lo corras.
      </template>
    </AlertBox>
    <ol class="mt-4 list-decimal space-y-1 pl-5 text-sm text-ink/85">
      <li>
        Descarga el script y súbelo al MikroTik (<span class="font-medium">Files</span>). En la
        terminal: <code class="code">/import olterra-{{ scripts.router.name }}.rsc</code>. Pegarlo
        en la consola lo puede corromper.
      </li>
      <li>Puedes correrlo otra vez sin miedo: no duplica nada.</li>
      <li>
        {{
          isSstp
            ? 'En /interface sstp-client debe quedar conectado (R).'
            : 'En /interface wireguard peers, last-handshake debe ser reciente.'
        }}
      </li>
      <li>El segundo bloque es el alta en el concentrador y la aplica Olterra.</li>
    </ol>
    <div class="mt-4 space-y-4">
      <CopyBlock
        :text="scripts.isp_script"
        label="Script para el MikroTik del ISP"
        :filename="`olterra-${scripts.router.name}.rsc`"
      />
      <CopyBlock
        :text="scripts.hub_script"
        label="Alta en el concentrador"
        :filename="`concentrador-${scripts.router.name}.rsc`"
      />
    </div>
  </section>

  <AlertBox v-if="routers.error.value" tone="danger">{{ routers.error.value }}</AlertBox>
  <LoadingBlock v-else-if="routers.loading.value && !routers.data.value" />
  <EmptyState
    v-else-if="routers.data.value && routers.data.value.length === 0 && !showForm"
    :icon="Waypoints"
    title="Ningún MikroTik conectado"
    text="Agrega el MikroTik que tiene acceso a tus OLT. Olterra te da el script para correr en él."
  >
    <button v-if="canWrite" type="button" class="btn-primary" @click="showForm = true">
      <Plus class="size-4" />
      Agregar MikroTik
    </button>
  </EmptyState>

  <div v-else-if="routers.data.value && routers.data.value.length" class="card overflow-x-auto">
    <AlertBox v-if="rowError" tone="danger" class="m-4">{{ rowError }}</AlertBox>
    <table class="w-full text-sm">
      <thead class="table-head">
        <tr>
          <th class="px-4 py-2.5">MikroTik</th>
          <th class="px-4 py-2.5">IP en el túnel</th>
          <th class="px-4 py-2.5">Transporte</th>
          <th class="px-4 py-2.5">Identidad</th>
          <th class="px-4 py-2.5">Alta</th>
          <th class="px-4 py-2.5"><span class="sr-only">Acciones</span></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="item in routers.data.value" :key="item.id">
          <td class="px-4 py-3 font-medium">{{ item.name }}</td>
          <td class="px-4 py-3 font-mono text-xs">{{ item.overlay_ip }}</td>
          <td class="px-4 py-3">
            <StatusPill :tone="item.transport === 'sstp' ? 'info' : 'accent'">
              {{ item.transport === 'sstp' ? 'SSTP' : 'WireGuard' }}
            </StatusPill>
            <span v-if="item.routeros_version" class="ml-1.5 text-xs text-muted">
              v{{ item.routeros_version }}
            </span>
          </td>
          <td
            class="max-w-[12rem] truncate px-4 py-3 font-mono text-xs text-muted"
            :title="item.wg_public_key ?? item.ppp_user ?? ''"
          >
            {{ item.wg_public_key ?? item.ppp_user }}
          </td>
          <td class="px-4 py-3 text-muted" :title="formatDateTime(item.created_at)">
            {{ timeAgo(item.created_at) }}
          </td>
          <td class="px-4 py-3 text-right whitespace-nowrap">
            <template v-if="canWrite">
              <span v-if="confirming === item.id" class="inline-flex items-center gap-1">
                <select
                  v-model="rotateVersion"
                  class="input w-auto py-1 text-xs"
                  aria-label="RouterOS del router"
                >
                  <option v-for="option in VERSIONS" :key="option.value" :value="option.value">
                    {{ option.label }}
                  </option>
                </select>
                <button
                  type="button"
                  class="btn-primary px-2.5 py-1 text-xs"
                  :disabled="rotating === item.id"
                  @click="rotate(item)"
                >
                  <LoaderCircle v-if="rotating === item.id" class="size-3.5 animate-spin" />
                  {{ rotateVersion === major(item) ? 'Rotar ahora' : 'Cambiar y rotar' }}
                </button>
                <button
                  type="button"
                  class="btn-ghost px-2 py-1 text-xs"
                  @click="confirming = null"
                >
                  Cancelar
                </button>
              </span>
              <button
                v-else
                type="button"
                class="btn-ghost px-2 py-1 text-xs"
                title="Genera una credencial nueva y el script con las OLT de hoy; también cambia la versión de RouterOS"
                @click="askRotation(item)"
              >
                <RotateCw class="size-3.5" />
                Rotar o cambiar versión
              </button>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
