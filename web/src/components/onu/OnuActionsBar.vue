<script setup lang="ts">
import { Copy, EllipsisVertical, Layers, Pencil, Power, Trash2 } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  changeOnuProfiles,
  deleteOnu,
  rebootOnu,
  renameOnuOnOlt,
  setOnuCustomerName,
  type OnuStatus,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { onuName } from '@/lib/monitoring'
import { oltLabel } from '@/lib/oltText'
import { errorText } from '@/lib/useAsync'
import { useWrite } from '@/lib/useWrite'

const props = defineProps<{ status: OnuStatus; driver: string }>()
const emit = defineEmits<{ renamed: [status: OnuStatus] }>()
const router = useRouter()

const where = computed(() => ({ pon: props.status.pon, onu: props.status.onu }))
const who = computed(() => onuName(props.status))

type Dialog = 'rename' | 'reboot' | 'delete' | 'profiles' | null
const dialog = ref<Dialog>(null)
const menu = ref(false)

const write = useWrite({
  rename: {
    busy: 'Cambiando el nombre en la OLT…',
    ok: 'Listo: el nombre también quedó en la OLT.',
  },
  reboot: {
    busy: 'Pidiéndole a la OLT que reinicie la ONU…',
    ok: 'Listo: la ONU se está reiniciando. Vuelve en uno o dos minutos.',
  },
  delete: {
    busy: 'Eliminando la ONU de la OLT…',
    ok: 'Listo: la ONU quedó eliminada de la OLT. Sale de la lista en el próximo sondeo.',
  },
  profiles: { busy: 'Cambiando los perfiles…', ok: 'Listo: perfiles cambiados en la OLT.' },
})

function open(next: Exclude<Dialog, null>): void {
  menu.value = false
  write.reset()
  nameError.value = null
  if (next === 'rename') {
    nameDraft.value = props.status.customer_name ?? props.status.description ?? ''
    nameOnOlt.value = true
  }
  dialog.value = next
}

function close(): void {
  if (write.waiting.value || savingName.value) return
  const deleted = write.kind.value === 'delete' && write.state.value?.tone === 'success'
  dialog.value = null
  if (deleted) void router.push({ name: 'clients' })
}

// --- Renombrar: en Olterra siempre; en la OLT si se marca (sin espacios ni tildes) ----------
const nameDraft = ref('')
const nameOnOlt = ref(true)
const savingName = ref(false)
const nameError = ref<string | null>(null)
const oltName = computed(() => oltLabel(nameDraft.value, 64))

async function saveName(): Promise<void> {
  savingName.value = true
  nameError.value = null
  try {
    const saved = await setOnuCustomerName(
      props.status.olt_id,
      props.status.pon,
      props.status.onu,
      nameDraft.value.trim() || null,
    )
    emit('renamed', saved)
    if (nameOnOlt.value && saved.customer_name && oltName.value) {
      savingName.value = false
      const ok = await write.run('rename', () =>
        renameOnuOnOlt(props.status.olt_id, { ...where.value, description: oltName.value }),
      )
      if (ok) emit('renamed', { ...saved, description: oltName.value })
    } else {
      dialog.value = null
    }
  } catch (caught) {
    nameError.value = errorText(caught)
  } finally {
    savingName.value = false
  }
}

// --- Perfiles (HSGQ) ---------------------------------------------------------------------
const profiles = ref({ line: 0, srv: 0 })

async function confirm(): Promise<void> {
  if (dialog.value === 'reboot') {
    await write.run('reboot', () => rebootOnu(props.status.olt_id, where.value))
  } else if (dialog.value === 'delete') {
    await write.run('delete', () => deleteOnu(props.status.olt_id, where.value))
  } else if (dialog.value === 'profiles') {
    await write.run('profiles', () =>
      changeOnuProfiles(props.status.olt_id, {
        ...where.value,
        line_profile: profiles.value.line,
        srv_profile: profiles.value.srv,
      }),
    )
  }
}

const finished = computed(() => write.state.value?.tone === 'success')
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <button type="button" class="btn-secondary" @click="open('rename')">
      <Pencil class="size-4" />
      Renombrar
    </button>
    <button type="button" class="btn-secondary" @click="open('reboot')">
      <Power class="size-4" />
      Reiniciar
    </button>
    <button type="button" class="btn-secondary text-danger" @click="open('delete')">
      <Trash2 class="size-4" />
      Eliminar
    </button>
    <div class="relative">
      <button
        type="button"
        class="btn-ghost px-2"
        aria-label="Más acciones"
        :aria-expanded="menu"
        @click="menu = !menu"
      >
        <EllipsisVertical class="size-4" />
      </button>
      <div
        v-if="menu"
        class="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-line bg-surface py-1 text-sm shadow-lg"
        role="menu"
        @keydown.esc="menu = false"
      >
        <button
          v-if="driver === 'hsgq-gpon'"
          type="button"
          role="menuitem"
          class="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-subtle"
          @click="open('profiles')"
        >
          <Layers class="size-4 text-muted" />
          Cambiar perfiles
        </button>
        <RouterLink
          v-if="driver === 'vsol-gpon'"
          role="menuitem"
          :to="{
            name: 'template-new',
            query: { olt: status.olt_id, pon: status.pon, onu: status.onu },
          }"
          class="flex items-center gap-2 px-3 py-2 hover:bg-subtle"
        >
          <Copy class="size-4 text-muted" />
          Copiar como plan
        </RouterLink>
        <p v-if="driver === 'bcd-gpon'" class="px-3 py-2 text-xs text-muted">
          La BCD no cambia perfiles: se elimina y se autoriza con los nuevos.
        </p>
      </div>
      <div v-if="menu" class="fixed inset-0 z-10" aria-hidden="true" @click="menu = false" />
    </div>

    <!-- Renombrar -->
    <ConfirmDialog
      :open="dialog === 'rename'"
      title="Nombre del cliente"
      hide-confirm
      @close="close"
    >
      <form v-if="!write.kind.value" class="space-y-3" @submit.prevent="saveName">
        <div>
          <label for="nombre-cliente" class="label">Nombre</label>
          <input
            id="nombre-cliente"
            v-model="nameDraft"
            class="input"
            maxlength="64"
            placeholder="Nombre del cliente"
            autocomplete="off"
          />
          <p class="hint">Vacío quita el nombre de Olterra y vuelve a mostrar el de la OLT.</p>
        </div>
        <label class="flex items-start gap-2">
          <input v-model="nameOnOlt" type="checkbox" class="mt-0.5" />
          <span>
            Cambiarlo también en la OLT
            <span v-if="oltName" class="block text-xs text-muted">
              Allá queda como <span class="code">{{ oltName }}</span> (sin espacios ni tildes).
            </span>
          </span>
        </label>
        <AlertBox v-if="nameError" tone="danger">{{ nameError }}</AlertBox>
        <div class="flex justify-end gap-2">
          <button type="button" class="btn-ghost" @click="close">Cancelar</button>
          <button type="submit" class="btn-primary" :disabled="savingName">Guardar</button>
        </div>
      </form>
      <template v-else>
        <AlertBox
          :tone="
            write.state.value?.tone === 'danger'
              ? 'danger'
              : write.state.value?.tone === 'success'
                ? 'success'
                : 'info'
          "
        >
          Nombre guardado en Olterra. {{ write.state.value?.text }}
        </AlertBox>
        <div class="flex justify-end">
          <button
            type="button"
            class="btn-secondary"
            :disabled="write.waiting.value"
            @click="close"
          >
            Cerrar
          </button>
        </div>
      </template>
    </ConfirmDialog>

    <!-- Reiniciar, eliminar y perfiles -->
    <ConfirmDialog
      :open="dialog === 'reboot' || dialog === 'delete' || dialog === 'profiles'"
      :title="
        dialog === 'reboot'
          ? 'Reiniciar la ONU'
          : dialog === 'delete'
            ? 'Eliminar de la OLT'
            : 'Perfiles de la ONU'
      "
      :confirm-label="
        dialog === 'reboot' ? 'Sí, reiniciar' : dialog === 'delete' ? 'Sí, eliminar' : 'Aplicar'
      "
      :tone="dialog === 'delete' ? 'danger' : 'accent'"
      :busy="write.waiting.value"
      :hide-confirm="finished"
      @close="close"
      @confirm="confirm"
    >
      <template v-if="!write.kind.value">
        <p v-if="dialog === 'reboot'">
          ¿Reiniciar la ONU de <b>{{ who }}</b
          >? Se queda sin servicio uno o dos minutos y vuelve sola.
        </p>
        <template v-else-if="dialog === 'delete'">
          <p>
            ¿Eliminar a <b>{{ who }}</b> de la OLT? Se desautoriza y el cliente pierde el servicio.
          </p>
          <p class="text-muted">
            Para que vuelva hay que autorizarla de nuevo en <b>Autorizar ONU</b>. En VSOL, si tiene
            PPPoE configurado desde la OLT, ten a mano su clave: la OLT no la muestra.
          </p>
        </template>
        <div v-else class="grid grid-cols-2 gap-3">
          <p class="col-span-2 text-muted">
            Los números de perfil de línea y de servicio de la OLT (la de Tocaima usa 0 y 0).
          </p>
          <div>
            <label for="perfil-linea" class="label">Perfil de línea</label>
            <input
              id="perfil-linea"
              v-model.number="profiles.line"
              type="number"
              min="0"
              class="input"
            />
          </div>
          <div>
            <label for="perfil-servicio" class="label">Perfil de servicio</label>
            <input
              id="perfil-servicio"
              v-model.number="profiles.srv"
              type="number"
              min="0"
              class="input"
            />
          </div>
        </div>
      </template>
      <AlertBox
        v-else-if="write.state.value"
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
      <div v-if="finished" class="flex justify-end">
        <button type="button" class="btn-secondary" @click="close">
          {{ write.kind.value === 'delete' ? 'Volver a Clientes' : 'Cerrar' }}
        </button>
      </div>
    </ConfirmDialog>
  </div>
</template>
