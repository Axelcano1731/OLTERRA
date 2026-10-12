<script setup lang="ts">
import { LoaderCircle, ScrollText } from '@lucide/vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { listAudit, listOlts, type AuditEntry } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import { AUDIT_KINDS, auditAction, auditDetails } from '@/lib/audit'
import { formatDateTime } from '@/lib/format'
import { actorLabel } from '@/lib/labels'
import { errorText, useAsync } from '@/lib/useAsync'

const PAGE = 50

const route = useRoute()
const router = useRouter()
const olts = useAsync(listOlts)

const oltFilter = ref(typeof route.query.olt === 'string' ? route.query.olt : '')
const kindFilter = ref(typeof route.query.tipo === 'string' ? route.query.tipo : '')
const entries = ref<AuditEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const more = ref(false)

const oltNames = computed(
  () => new Map((olts.data.value ?? []).map((olt) => [olt.id, olt.name] as const)),
)

function target(entry: AuditEntry): string | null {
  if (entry.target_type === 'olt' && entry.target_id) {
    return oltNames.value.get(entry.target_id) ?? 'OLT eliminada'
  }
  return null
}

async function load(reset: boolean): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const last = reset ? undefined : entries.value.at(-1)?.id
    const page = await listAudit({
      limit: PAGE,
      before: last,
      target_id: oltFilter.value || undefined,
      action: kindFilter.value || undefined,
    })
    entries.value = reset ? page : [...entries.value, ...page]
    more.value = page.length === PAGE
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    loading.value = false
  }
}

watch([oltFilter, kindFilter], ([olt, kind]) => {
  void router.replace({ query: { ...(olt ? { olt } : {}), ...(kind ? { tipo: kind } : {}) } })
  void load(true)
})
onMounted(() => void load(true))
</script>

<template>
  <PageHeader
    title="Bitácora"
    description="Quién hizo qué, cuándo y desde dónde. Nunca guarda claves: de las credenciales solo anota cuáles cambiaron."
  />

  <div class="mb-6 flex flex-wrap gap-4">
    <div>
      <label for="olt" class="label">OLT</label>
      <select id="olt" v-model="oltFilter" class="input">
        <option value="">Todas</option>
        <option v-for="olt in olts.data.value ?? []" :key="olt.id" :value="olt.id">
          {{ olt.name }}
        </option>
      </select>
    </div>
    <div>
      <label for="kind" class="label">Tipo</label>
      <select id="kind" v-model="kindFilter" class="input">
        <option v-for="kind in AUDIT_KINDS" :key="kind.value" :value="kind.value">
          {{ kind.label }}
        </option>
      </select>
    </div>
  </div>

  <AlertBox v-if="error" tone="danger" class="mb-6">{{ error }}</AlertBox>
  <LoadingBlock v-if="loading && entries.length === 0" />
  <EmptyState
    v-else-if="!loading && entries.length === 0"
    :icon="ScrollText"
    title="Nada con estos filtros"
    text="Cada cambio que se haga en Olterra queda aquí."
  />

  <template v-else>
    <ol class="card divide-y divide-line">
      <li
        v-for="entry in entries"
        :key="entry.id"
        class="grid gap-1 px-5 py-4 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4"
      >
        <div class="text-xs text-muted">
          <p>{{ formatDateTime(entry.created_at) }}</p>
          <p v-if="entry.source_ip">desde {{ entry.source_ip }}</p>
        </div>
        <div class="min-w-0">
          <p class="text-sm">
            <span class="font-medium">{{ actorLabel(entry.actor) }}</span>
            · {{ auditAction(entry.action) }}
            <template v-if="target(entry)">
              ·
              <RouterLink
                v-if="entry.target_id && oltNames.has(entry.target_id)"
                :to="{ name: 'olt', params: { id: entry.target_id } }"
                class="font-medium underline"
                >{{ target(entry) }}</RouterLink
              >
              <span v-else class="text-muted">{{ target(entry) }}</span>
            </template>
          </p>
          <dl
            v-if="auditDetails(entry).length"
            class="mt-1 grid gap-x-4 gap-y-0.5 text-xs sm:grid-cols-2"
          >
            <div v-for="item in auditDetails(entry)" :key="item.label" class="flex min-w-0 gap-1">
              <dt class="shrink-0 text-muted">{{ item.label }}:</dt>
              <dd class="min-w-0 truncate">
                <template v-if="item.before !== undefined">
                  <span class="text-muted line-through">{{ item.before }}</span> →
                </template>
                {{ item.value }}
              </dd>
            </div>
          </dl>
        </div>
      </li>
    </ol>
    <div v-if="more" class="mt-4 flex justify-center">
      <button type="button" class="btn-secondary" :disabled="loading" @click="load(false)">
        <LoaderCircle v-if="loading" class="size-4 animate-spin" />
        Ver más
      </button>
    </div>
  </template>
</template>
