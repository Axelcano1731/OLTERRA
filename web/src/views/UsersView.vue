<script setup lang="ts">
import { KeyRound, LoaderCircle, Plus, Save, UserRound, X } from '@lucide/vue'
import { computed, ref, shallowRef, watch } from 'vue'

import {
  createUser,
  listUsers,
  renameTenant,
  resetUserPassword,
  updateUser,
  type IspUser,
  type IspUserWithPassword,
  type UserRole,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import CopyBlock from '@/components/CopyBlock.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { errorText, useAsync } from '@/lib/useAsync'
import { can, refreshMe, session } from '@/session'

const NAME = /^[A-Za-z0-9_.-]{3,32}$/

const ROLES: { value: UserRole; label: string; text: string }[] = [
  { value: 'admin', label: 'Admin', text: 'Todo, incluso usuarios y túnel' },
  { value: 'tecnico', label: 'Técnico', text: 'Consulta, aprovisiona y edita el mapa' },
  { value: 'lectura', label: 'Solo lectura', text: 'Consulta y monitoreo' },
]

const users = useAsync(listUsers)
const canWrite = computed(() => can('users:write'))
const me = computed(() => session.me?.user?.username ?? null)

// --- Nombre del ISP ---------------------------------------------------------------------------
const ispName = ref(session.me?.tenant.name ?? '')
watch(
  () => session.me?.tenant.name,
  (name) => {
    if (name) ispName.value = name
  },
)
const savingIsp = ref(false)
const ispMessage = ref<{ tone: 'success' | 'danger'; text: string } | null>(null)

async function saveIsp(): Promise<void> {
  ispMessage.value = null
  if (ispName.value.trim().length < 2) {
    ispMessage.value = { tone: 'danger', text: 'El nombre necesita al menos 2 caracteres.' }
    return
  }
  savingIsp.value = true
  try {
    const tenant = await renameTenant(ispName.value.trim())
    await refreshMe()
    ispMessage.value = { tone: 'success', text: `Ahora el ISP se llama ${tenant.name}.` }
  } catch (caught) {
    ispMessage.value = { tone: 'danger', text: errorText(caught) }
  } finally {
    savingIsp.value = false
  }
}

// --- Alta --------------------------------------------------------------------------------------
const showForm = ref(false)
const form = ref({ username: '', displayName: '', role: 'tecnico' })
const creating = ref(false)
const formError = ref<string | null>(null)
// La contraseña inicial vive solo en memoria hasta que se cierra el aviso.
const issued = shallowRef<{ result: IspUserWithPassword; reason: 'created' | 'reset' } | null>(null)

async function create(): Promise<void> {
  formError.value = null
  if (!NAME.test(form.value.username.trim())) {
    formError.value = 'El usuario va sin espacios: de 3 a 32 letras, números, punto o guion.'
    return
  }
  creating.value = true
  try {
    const result = await createUser(
      form.value.username.trim(),
      form.value.displayName.trim(),
      form.value.role,
    )
    issued.value = { result, reason: 'created' }
    form.value = { username: '', displayName: '', role: 'tecnico' }
    showForm.value = false
    await users.reload()
  } catch (caught) {
    formError.value = errorText(caught)
  } finally {
    creating.value = false
  }
}

// --- Cambios en una fila ------------------------------------------------------------------------
const busy = ref<string | null>(null)
const rowError = ref<string | null>(null)

async function change(user: IspUser, changes: Parameters<typeof updateUser>[1]): Promise<void> {
  rowError.value = null
  busy.value = user.id
  try {
    await updateUser(user.id, changes)
    await users.reload()
  } catch (caught) {
    rowError.value = errorText(caught)
    await users.reload()
  } finally {
    busy.value = null
  }
}

async function reset(user: IspUser): Promise<void> {
  rowError.value = null
  busy.value = user.id
  try {
    issued.value = { result: await resetUserPassword(user.id), reason: 'reset' }
    await users.reload()
  } catch (caught) {
    rowError.value = errorText(caught)
  } finally {
    busy.value = null
  }
}

function state(user: IspUser): {
  label: string
  tone: 'success' | 'warning' | 'danger' | 'neutral'
} {
  if (user.disabled) return { label: 'Deshabilitado', tone: 'neutral' }
  if (user.locked) return { label: 'Bloqueado 15 min', tone: 'danger' }
  if (user.must_change_password) return { label: 'Sin estrenar', tone: 'warning' }
  return { label: 'Activo', tone: 'success' }
}
</script>

<template>
  <PageHeader
    title="Usuarios"
    description="Las personas de tu ISP que entran a Olterra y lo que puede hacer cada una."
  >
    <template #actions>
      <button
        v-if="canWrite && !showForm"
        type="button"
        class="btn-primary"
        @click="showForm = true"
      >
        <Plus class="size-4" />
        Agregar usuario
      </button>
    </template>
  </PageHeader>

  <section v-if="can('tenant:write')" class="card mb-6 p-5 sm:p-6" aria-labelledby="isp">
    <h2 id="isp" class="font-semibold">Tu ISP</h2>
    <form class="mt-3 flex flex-wrap items-end gap-3" @submit.prevent="saveIsp">
      <div class="min-w-0 flex-1 sm:max-w-sm">
        <label for="isp-name" class="label">Nombre</label>
        <input id="isp-name" v-model="ispName" class="input" maxlength="64" />
      </div>
      <button type="submit" class="btn-secondary" :disabled="savingIsp">
        <LoaderCircle v-if="savingIsp" class="size-4 animate-spin" />
        <Save v-else class="size-4" />
        Guardar
      </button>
    </form>
    <p class="hint mt-2">
      Es el nombre que ven tus usuarios. El identificador interno ({{ session.me?.tenant.slug }}) no
      cambia: va en los scripts de tus MikroTik.
    </p>
    <AlertBox v-if="ispMessage" :tone="ispMessage.tone" class="mt-3">{{
      ispMessage.text
    }}</AlertBox>
  </section>

  <section v-if="showForm" class="card mb-6 p-5 sm:p-6" aria-labelledby="nuevo-usuario">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h2 id="nuevo-usuario" class="font-semibold">Agregar un usuario</h2>
        <p class="mt-1 text-sm text-muted">
          Olterra le genera una contraseña inicial; la cambia la primera vez que entre.
        </p>
      </div>
      <button type="button" class="btn-ghost p-1.5" aria-label="Cerrar" @click="showForm = false">
        <X class="size-4" />
      </button>
    </div>
    <form
      class="mt-4 grid gap-4 sm:grid-cols-[1fr_1fr_14rem_auto] sm:items-end"
      @submit.prevent="create"
    >
      <div>
        <label for="new-username" class="label">Usuario</label>
        <input
          id="new-username"
          v-model="form.username"
          class="input"
          placeholder="juan.perez"
          maxlength="32"
          autocomplete="off"
        />
      </div>
      <div>
        <label for="new-name" class="label">Nombre</label>
        <input id="new-name" v-model="form.displayName" class="input" placeholder="Juan Pérez" />
      </div>
      <div>
        <label for="new-role" class="label">Rol</label>
        <select id="new-role" v-model="form.role" class="input">
          <option v-for="role in ROLES" :key="role.value" :value="role.value">
            {{ role.label }} — {{ role.text }}
          </option>
        </select>
      </div>
      <button type="submit" class="btn-primary" :disabled="creating">
        <LoaderCircle v-if="creating" class="size-4 animate-spin" />
        <UserRound v-else class="size-4" />
        Crear
      </button>
    </form>
    <AlertBox v-if="formError" tone="danger" class="mt-4">{{ formError }}</AlertBox>
  </section>

  <AlertBox
    v-if="issued"
    tone="warning"
    class="mb-6"
    :title="
      issued.reason === 'created'
        ? `Usuario ${issued.result.user.username} creado`
        : `Contraseña nueva para ${issued.result.user.username}`
    "
  >
    <p>
      Dásela a {{ issued.result.user.display_name }}: la tendrá que cambiar al entrar. Se muestra
      una sola vez.
    </p>
    <div class="mt-3 max-w-md">
      <CopyBlock :text="issued.result.initial_password" label="Contraseña inicial" />
    </div>
    <button type="button" class="btn-secondary mt-3" @click="issued = null">Ya la entregué</button>
  </AlertBox>

  <AlertBox v-if="users.error.value" tone="danger">{{ users.error.value }}</AlertBox>
  <LoadingBlock v-else-if="users.loading.value && !users.data.value" />
  <div v-else-if="users.data.value" class="card overflow-x-auto">
    <AlertBox v-if="rowError" tone="danger" class="m-4">{{ rowError }}</AlertBox>
    <table class="w-full text-sm">
      <thead class="table-head">
        <tr>
          <th class="px-4 py-2.5">Usuario</th>
          <th class="px-4 py-2.5">Rol</th>
          <th class="px-4 py-2.5">Estado</th>
          <th class="hidden px-4 py-2.5 md:table-cell">Última entrada</th>
          <th class="px-4 py-2.5"><span class="sr-only">Acciones</span></th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="user in users.data.value" :key="user.id" :class="user.disabled && 'opacity-60'">
          <td class="px-4 py-3">
            <span class="block font-medium">{{ user.display_name }}</span>
            <span class="font-mono text-xs text-muted">{{ user.username }}</span>
            <span v-if="user.username === me" class="ml-1 text-xs text-muted">(tú)</span>
          </td>
          <td class="px-4 py-3">
            <select
              v-if="canWrite && user.username !== me"
              :value="user.role"
              class="input w-auto py-1 text-xs"
              :disabled="busy === user.id"
              :aria-label="`Rol de ${user.username}`"
              @change="
                change(user, { role: ($event.target as HTMLSelectElement).value as UserRole })
              "
            >
              <option v-for="role in ROLES" :key="role.value" :value="role.value">
                {{ role.label }}
              </option>
            </select>
            <span v-else>{{ ROLES.find((r) => r.value === user.role)?.label ?? user.role }}</span>
          </td>
          <td class="px-4 py-3">
            <StatusPill :tone="state(user).tone">{{ state(user).label }}</StatusPill>
          </td>
          <td
            class="hidden px-4 py-3 text-muted md:table-cell"
            :title="user.last_login_at ? formatDateTime(user.last_login_at) : undefined"
          >
            {{ user.last_login_at ? timeAgo(user.last_login_at) : 'Nunca' }}
          </td>
          <td class="px-4 py-3 text-right whitespace-nowrap">
            <template v-if="canWrite && user.username !== me">
              <button
                type="button"
                class="btn-ghost px-2 py-1 text-xs"
                :disabled="busy === user.id"
                title="Le genera una contraseña inicial nueva y cierra sus sesiones"
                @click="reset(user)"
              >
                <KeyRound class="size-3.5" />
                Restablecer contraseña
              </button>
              <button
                type="button"
                class="btn-ghost px-2 py-1 text-xs"
                :disabled="busy === user.id"
                @click="change(user, { disabled: !user.disabled })"
              >
                {{ user.disabled ? 'Habilitar' : 'Deshabilitar' }}
              </button>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
