<script setup lang="ts">
import { ChevronDown, CircleCheck, Download, FileText, Search, SearchX } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'

import { getRecon, type Severity } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import {
  filterFindings,
  findingsToCsv,
  groupFindings,
  refChips,
  SEVERITIES,
  SEVERITY_LABEL,
} from '@/lib/findings'
import { formatDateTime, plural } from '@/lib/format'
import { actorLabel, FILE_KIND, RECON_SOURCE, type Tone } from '@/lib/labels'
import { useAsync } from '@/lib/useAsync'

const props = defineProps<{ id: string }>()
const run = useAsync(() => getRecon(props.id))

const SEVERITY_TONE: Record<Severity, Tone> = {
  error: 'danger',
  advertencia: 'warning',
  info: 'info',
}

const SEVERITY_CARD: Record<Severity, string> = {
  error: 'text-danger',
  advertencia: 'text-warning',
  info: 'text-info',
}

const active = reactive(new Set<Severity>(SEVERITIES))
const search = ref('')
const collapsed = reactive(new Set<string>())

const findings = computed(() => run.data.value?.findings ?? [])
const visible = computed(() => filterFindings(findings.value, active, search.value))
const groups = computed(() => groupFindings(visible.value))

function count(severity: Severity): number {
  return findings.value.filter((finding) => finding.severity === severity).length
}

function toggleSeverity(severity: Severity): void {
  if (active.has(severity)) active.delete(severity)
  else active.add(severity)
}

function toggleGroup(kind: string): void {
  if (collapsed.has(kind)) collapsed.delete(kind)
  else collapsed.add(kind)
}

function exportCsv(): void {
  const data = run.data.value
  if (!data) return
  const url = URL.createObjectURL(
    new Blob([findingsToCsv(visible.value)], { type: 'text/csv;charset=utf-8' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = `conciliacion-${data.created_at.slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

function resetFilters(): void {
  SEVERITIES.forEach((severity) => active.add(severity))
  search.value = ''
}
</script>

<template>
  <LoadingBlock v-if="run.loading.value && !run.data.value" />
  <AlertBox v-else-if="run.error.value" tone="danger">{{ run.error.value }}</AlertBox>

  <template v-else-if="run.data.value">
    <PageHeader
      :title="`Conciliación del ${formatDateTime(run.data.value.created_at)}`"
      :back="{ name: 'recons' }"
      back-label="Conciliación"
    >
      <template #meta>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusPill :tone="run.data.value.source === 'demo' ? 'accent' : 'neutral'">
            {{ RECON_SOURCE[run.data.value.source] }}
          </StatusPill>
          <span>{{ actorLabel(run.data.value.requested_by) }}</span>
          <span class="w-full sm:w-auto">
            {{ plural(run.data.value.counts.onus ?? 0, 'ONU', 'ONUs') }} ·
            {{ plural(run.data.value.counts.secretos ?? 0, 'secreto', 'secretos') }} ·
            {{ plural(run.data.value.counts.sesiones ?? 0, 'sesión', 'sesiones') }} ·
            {{ plural(run.data.value.counts.clientes ?? 0, 'cliente', 'clientes') }}
          </span>
        </div>
      </template>
      <template #actions>
        <button
          type="button"
          class="btn-secondary"
          :disabled="visible.length === 0"
          @click="exportCsv"
        >
          <Download class="size-4" />
          Exportar CSV
        </button>
      </template>
    </PageHeader>

    <AlertBox v-if="run.data.value.source === 'demo'" tone="info" class="mb-6">
      Datos sintéticos: un caso por cada tipo de descuadre que detecta el motor. Ningún dato es de
      un ISP real.
    </AlertBox>

    <ul v-if="run.data.value.files.length" class="mb-6 flex flex-wrap gap-2">
      <li
        v-for="file in run.data.value.files"
        :key="`${file.kind}-${file.name}`"
        class="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1 text-xs"
      >
        <FileText class="size-3.5 text-muted" />
        <span class="font-medium">{{ file.name }}</span>
        <span class="text-muted">{{ FILE_KIND[file.kind] }} · {{ file.records }}</span>
      </li>
    </ul>

    <div class="grid grid-cols-3 gap-2 sm:gap-4">
      <button
        v-for="severity in SEVERITIES"
        :key="severity"
        type="button"
        :aria-pressed="active.has(severity)"
        title="Mostrar u ocultar"
        :class="[
          'card p-3 text-left transition-opacity sm:p-5',
          active.has(severity) ? '' : 'opacity-45',
        ]"
        @click="toggleSeverity(severity)"
      >
        <p class="text-xs text-muted sm:text-sm">{{ SEVERITY_LABEL[severity] }}</p>
        <p
          :class="[
            'mt-1 text-2xl font-semibold tabular-nums sm:mt-2 sm:text-3xl',
            SEVERITY_CARD[severity],
          ]"
        >
          {{ count(severity) }}
        </p>
      </button>
    </div>

    <EmptyState
      v-if="findings.length === 0"
      class="mt-6"
      :icon="CircleCheck"
      title="Todo cuadra"
      text="OLT, MikroTik y CRM dicen lo mismo. Ningún descuadre."
    />

    <template v-else>
      <div class="mt-6 flex flex-wrap items-center gap-3">
        <div class="relative min-w-[14rem] flex-1">
          <Search
            class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted"
          />
          <input
            v-model="search"
            type="search"
            class="input pl-9"
            placeholder="Buscar cliente, serial, usuario PPPoE…"
            aria-label="Buscar en los hallazgos"
          />
        </div>
        <p class="text-sm text-muted">
          {{ plural(visible.length, 'hallazgo', 'hallazgos') }} de {{ findings.length }}
        </p>
      </div>

      <EmptyState
        v-if="groups.length === 0"
        class="mt-4"
        :icon="SearchX"
        title="Nada coincide con el filtro"
      >
        <button type="button" class="btn-secondary" @click="resetFilters">Quitar filtros</button>
      </EmptyState>

      <div v-else class="mt-4 space-y-3">
        <section v-for="group in groups" :key="group.kind" class="card overflow-hidden">
          <button
            type="button"
            class="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-subtle/60"
            :aria-expanded="!collapsed.has(group.kind)"
            @click="toggleGroup(group.kind)"
          >
            <span
              :class="[
                'size-2.5 shrink-0 rounded-full',
                group.severity === 'error'
                  ? 'bg-danger'
                  : group.severity === 'advertencia'
                    ? 'bg-warning'
                    : 'bg-info',
              ]"
              aria-hidden="true"
            />
            <span class="min-w-0 flex-1 font-medium">{{ group.title }}</span>
            <StatusPill :tone="SEVERITY_TONE[group.severity]">{{
              group.findings.length
            }}</StatusPill>
            <ChevronDown
              :class="[
                'size-4 shrink-0 text-muted transition-transform',
                collapsed.has(group.kind) && '-rotate-90',
              ]"
            />
          </button>
          <ul v-if="!collapsed.has(group.kind)" class="divide-y divide-line border-t border-line">
            <li v-for="(finding, index) in group.findings" :key="index" class="px-5 py-4">
              <p class="text-sm text-ink">{{ finding.detail }}</p>
              <p class="mt-1.5 text-sm text-ink/85">
                <span class="font-medium text-accent">Qué hacer:</span> {{ finding.suggestion }}
              </p>
              <div v-if="refChips(finding).length" class="mt-2.5 flex flex-wrap gap-1.5">
                <span
                  v-for="chip in refChips(finding)"
                  :key="chip.label"
                  class="inline-flex items-center gap-1 rounded-md bg-subtle px-2 py-0.5 text-xs"
                >
                  <span class="text-muted">{{ chip.label }}</span>
                  <span class="font-mono">{{ chip.value }}</span>
                </span>
              </div>
            </li>
          </ul>
        </section>
      </div>
    </template>
  </template>
</template>
