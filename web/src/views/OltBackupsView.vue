<script setup lang="ts">
import { Archive, Download, LoaderCircle, RefreshCw } from '@lucide/vue'
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'

import {
  backupNow,
  diffBackup,
  getBackup,
  getOlt,
  listBackups,
  type Backup,
  type BackupDetail,
  type BackupDiff,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, plural, timeAgo } from '@/lib/format'
import { actorLabel, type Tone } from '@/lib/labels'
import { errorText, useAsync } from '@/lib/useAsync'

const props = defineProps<{ id: string }>()
const olt = useAsync(() => getOlt(props.id))
const list = useAsync(() => listBackups(props.id))

const selectedId = ref<string | null>(null)
const tab = ref<'diff' | 'full'>('diff')
const detail = shallowRef<BackupDetail | null>(null)
const diff = shallowRef<BackupDiff | null>(null)
const opening = ref(false)
const openError = ref<string | null>(null)
const starting = ref(false)
const startError = ref<string | null>(null)
// El que se pidió con "Respaldar ahora": se abre apenas termine.
const awaited = ref<string | null>(null)

const pending = computed(() => list.data.value?.some((b) => b.status === 'pending') ?? false)
const selected = computed(() => list.data.value?.find((b) => b.id === selectedId.value) ?? null)

// Mientras haya uno en curso, la lista se refresca sola.
let timer: number | undefined
watch(
  () => list.data.value,
  (rows) => {
    window.clearTimeout(timer)
    if (rows?.some((b) => b.status === 'pending')) {
      timer = window.setTimeout(() => void list.reload(), 3000)
    }
    const done = rows?.find((b) => b.id === awaited.value && b.status !== 'pending')
    if (done) {
      awaited.value = null
      if (done.status === 'ok') void open(done)
      return
    }
    // Abre el más reciente que tenga configuración.
    if (rows && (!selectedId.value || !rows.some((b) => b.id === selectedId.value))) {
      const first = rows.find((b) => b.status === 'ok')
      if (first) void open(first)
    }
  },
)
onBeforeUnmount(() => window.clearTimeout(timer))

function badge(backup: Backup): { label: string; tone: Tone } {
  if (backup.status === 'pending') return { label: 'En curso', tone: 'accent' }
  if (backup.status === 'failed') return { label: 'No se pudo', tone: 'danger' }
  if (!backup.changed) return { label: 'Sin cambios', tone: 'neutral' }
  return { label: `+${backup.added ?? 0} −${backup.removed ?? 0}`, tone: 'info' }
}

function origin(backup: Backup): string {
  return backup.trigger === 'auto' ? 'Automático' : `A pedido · ${actorLabel(backup.requested_by)}`
}

async function open(backup: Backup): Promise<void> {
  if (backup.status !== 'ok') return
  selectedId.value = backup.id
  opening.value = true
  openError.value = null
  try {
    const [full, changes] = await Promise.all([getBackup(backup.id), diffBackup(backup.id)])
    if (selectedId.value !== backup.id) return
    detail.value = full
    diff.value = changes
    if (changes.from_id === null) tab.value = 'full'
  } catch (caught) {
    openError.value = errorText(caught)
  } finally {
    opening.value = false
  }
}

async function start(): Promise<void> {
  starting.value = true
  startError.value = null
  try {
    awaited.value = (await backupNow(props.id)).id
    await list.reload()
  } catch (caught) {
    startError.value = errorText(caught)
  } finally {
    starting.value = false
  }
}

function download(): void {
  const current = detail.value
  if (!current || !olt.data.value) return
  const stamp = current.created_at.slice(0, 16).replace(/[:T]/g, '-')
  const blob = new Blob([current.content], { type: 'text/plain;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `${olt.data.value.name}-${stamp}.txt`
  link.click()
  URL.revokeObjectURL(link.href)
}

const LINE_CLASS: Record<string, string> = {
  '+': 'bg-success-soft text-success',
  '-': 'bg-danger-soft text-danger',
  '@': 'text-muted bg-subtle',
  ' ': 'text-ink',
}
</script>

<template>
  <LoadingBlock v-if="olt.loading.value && !olt.data.value" />
  <AlertBox v-else-if="olt.error.value" tone="danger">{{ olt.error.value }}</AlertBox>

  <template v-else-if="olt.data.value">
    <PageHeader
      :title="`Respaldos de ${olt.data.value.name}`"
      description="Olterra guarda la configuración de la OLT cada día y cuando lo pides, y muestra qué cambió entre una y otra. Las claves salen tapadas."
      :back="{ name: 'olt', params: { id } }"
      :back-label="olt.data.value.name"
    >
      <template #actions>
        <button type="button" class="btn-primary" :disabled="starting || pending" @click="start">
          <LoaderCircle v-if="starting || pending" class="size-4 animate-spin" />
          <Archive v-else class="size-4" />
          {{ pending ? 'Respaldando…' : 'Respaldar ahora' }}
        </button>
      </template>
    </PageHeader>

    <AlertBox v-if="startError" tone="danger" class="mb-6">{{ startError }}</AlertBox>
    <AlertBox v-if="list.error.value" tone="danger" class="mb-6">{{ list.error.value }}</AlertBox>

    <LoadingBlock v-if="list.loading.value && !list.data.value" />
    <EmptyState
      v-else-if="list.data.value && list.data.value.length === 0"
      :icon="Archive"
      title="Todavía no hay respaldos"
      text="El primero se hace solo cuando la OLT responde a una consulta, o ahora con Respaldar ahora."
    />

    <div
      v-else-if="list.data.value"
      class="grid gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]"
    >
      <ul class="card divide-y divide-line self-start">
        <li v-for="backup in list.data.value" :key="backup.id">
          <button
            type="button"
            :class="[
              'flex w-full items-center gap-3 px-4 py-3 text-left',
              backup.status === 'ok' ? 'hover:bg-subtle' : 'cursor-default',
              backup.id === selectedId ? 'bg-accent-soft' : '',
            ]"
            :aria-current="backup.id === selectedId ? 'true' : undefined"
            @click="open(backup)"
          >
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium">{{ formatDateTime(backup.created_at) }}</span>
              <span class="block truncate text-xs text-muted">{{ origin(backup) }}</span>
              <span v-if="backup.error" class="block text-xs text-danger">{{ backup.error }}</span>
            </span>
            <StatusPill :tone="badge(backup).tone">{{ badge(backup).label }}</StatusPill>
          </button>
        </li>
      </ul>

      <section class="card min-w-0">
        <div v-if="!selected" class="p-6 text-sm text-muted">
          Elige un respaldo para ver su configuración y qué cambió.
        </div>
        <template v-else>
          <div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
            <div class="min-w-0 flex-1">
              <h2 class="font-semibold">{{ formatDateTime(selected.created_at) }}</h2>
              <p class="text-xs text-muted">
                {{ timeAgo(selected.created_at) }}
                <template v-if="selected.line_count !== null">
                  · {{ plural(selected.line_count, 'línea', 'líneas') }}</template
                >
                <template v-if="diff && diff.from_created_at">
                  · comparado con el del {{ formatDateTime(diff.from_created_at) }}</template
                >
              </p>
            </div>
            <div class="flex rounded-lg border border-line p-0.5 text-sm" role="tablist">
              <button
                type="button"
                role="tab"
                :aria-selected="tab === 'diff'"
                :class="['rounded-md px-3 py-1', tab === 'diff' ? 'bg-subtle font-medium' : '']"
                @click="tab = 'diff'"
              >
                Cambios
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="tab === 'full'"
                :class="['rounded-md px-3 py-1', tab === 'full' ? 'bg-subtle font-medium' : '']"
                @click="tab = 'full'"
              >
                Completa
              </button>
            </div>
            <button type="button" class="btn-secondary" :disabled="!detail" @click="download">
              <Download class="size-4" />
              Descargar
            </button>
          </div>

          <div v-if="opening" class="flex items-center gap-2 p-6 text-sm text-muted">
            <RefreshCw class="size-4 animate-spin" /> Abriendo…
          </div>
          <AlertBox v-else-if="openError" tone="danger" class="m-5">{{ openError }}</AlertBox>

          <template v-else-if="tab === 'diff' && diff">
            <p v-if="diff.from_id === null" class="p-5 text-sm text-muted">
              Es el primer respaldo de esta OLT: no hay con qué compararlo. Mira la configuración en
              Completa.
            </p>
            <p v-else-if="diff.lines.length === 0" class="p-5 text-sm text-muted">
              Sin cambios respecto al respaldo anterior.
            </p>
            <div v-else class="overflow-x-auto p-3 font-mono text-xs leading-5">
              <p class="mb-2 px-2 font-sans text-sm">
                <span class="text-success">+{{ diff.added }}</span>
                <span class="ml-2 text-danger">−{{ diff.removed }}</span>
              </p>
              <div
                v-for="(line, index) in diff.lines"
                :key="index"
                :class="['px-2 whitespace-pre', LINE_CLASS[line.kind]]"
                v-text="`${line.kind === '@' ? '⋯' : line.kind} ${line.text}`"
              />
            </div>
          </template>

          <pre
            v-else-if="tab === 'full' && detail"
            class="max-h-[70vh] overflow-auto p-5 font-mono text-xs leading-5 text-ink"
            v-text="detail.content"
          />
        </template>
      </section>
    </div>
  </template>
</template>
