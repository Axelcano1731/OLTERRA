<script setup lang="ts">
import { GitCompareArrows, LoaderCircle } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { runReconFiles, type ReconFileKind } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import FileDrop from '@/components/FileDrop.vue'
import PageHeader from '@/components/PageHeader.vue'
import { FILE_KIND } from '@/lib/labels'
import { errorText } from '@/lib/useAsync'

const router = useRouter()

// Lo mismo que acepta `olterra-conciliar correr` (reconciliation/sources).
const SOURCES: { kind: ReconFileKind; hint: string; accept: string }[] = [
  {
    kind: 'onus',
    hint: 'CSV con el serial; si los tienes: olt, pon, onu, estado, pppoe y macs.',
    accept: '.csv,.tsv,text/csv',
  },
  {
    kind: 'secrets',
    hint: 'CSV o el texto de /ppp secret export. Las claves se descartan al leer.',
    accept: '.csv,.tsv,.txt,.rsc,text/csv,text/plain',
  },
  {
    kind: 'sessions',
    hint: 'CSV o el texto de /ppp active print terse.',
    accept: '.csv,.tsv,.txt,text/csv,text/plain',
  },
  {
    kind: 'customers',
    hint: 'CSV de ISPWatch, WispHub, Mikrowisp o Excel. Acepta ; y tildes.',
    accept: '.csv,.tsv,text/csv',
  },
]

const files = reactive<Record<ReconFileKind, File[]>>({
  onus: [],
  secrets: [],
  sessions: [],
  customers: [],
})
const routerName = ref('')
const busy = ref(false)
const error = ref<string | null>(null)

const total = computed(() => Object.values(files).reduce((sum, list) => sum + list.length, 0))

async function submit(): Promise<void> {
  error.value = null
  if (total.value === 0) return
  const form = new FormData()
  for (const source of SOURCES) {
    for (const file of files[source.kind]) form.append(source.kind, file, file.name)
  }
  if (routerName.value.trim()) form.append('router_name', routerName.value.trim())
  busy.value = true
  try {
    const run = await runReconFiles(form)
    await router.push({ name: 'recon', params: { id: run.id } })
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader
    title="Nueva conciliación"
    description="Sube lo que tengas: cada fuente que agregues destapa más descuadres. De cada archivo solo se guarda el nombre y cuántos registros traía."
    :back="{ name: 'recons' }"
    back-label="Conciliación"
  />

  <form class="space-y-6" @submit.prevent="submit">
    <div class="card grid gap-6 p-5 sm:p-6 md:grid-cols-2">
      <FileDrop
        v-for="source in SOURCES"
        :key="source.kind"
        v-model="files[source.kind]"
        :label="FILE_KIND[source.kind]"
        :hint="source.hint"
        :accept="source.accept"
      />
    </div>

    <div class="card p-5 sm:p-6">
      <label for="router-name" class="label">
        Nombre del MikroTik <span class="font-normal text-muted">(opcional)</span>
      </label>
      <input
        id="router-name"
        v-model="routerName"
        class="input max-w-sm"
        placeholder="BNG-CENTRO"
        maxlength="64"
      />
      <p class="hint">
        Para los textos copiados del MikroTik. Vacío, se usa el nombre de cada archivo.
      </p>
    </div>

    <AlertBox v-if="error" tone="danger" title="No se pudo conciliar">{{ error }}</AlertBox>

    <div class="flex items-center justify-end gap-3">
      <p class="text-sm text-muted">
        {{ total === 0 ? 'Ningún archivo todavía' : `${total} archivo${total === 1 ? '' : 's'}` }}
      </p>
      <button type="submit" class="btn-primary" :disabled="busy || total === 0">
        <LoaderCircle v-if="busy" class="size-4 animate-spin" />
        <GitCompareArrows v-else class="size-4" />
        Conciliar
      </button>
    </div>
  </form>
</template>
