<script setup lang="ts">
// Datos que devuelve un parser: lista de filas → tabla; objeto → pares; otra cosa → texto.
import { computed } from 'vue'

const props = defineProps<{ data: unknown }>()

type Row = Record<string, unknown>

function isRow(value: unknown): value is Row {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const table = computed(() => {
  const data = props.data
  if (!Array.isArray(data) || data.length === 0 || !data.every(isRow)) return null
  // Una columna que solo trae objetos (como `raw`, la fila sin interpretar) no cabe en una
  // celda y empuja las demás fuera de la vista; ese texto ya está en "Salida de la OLT".
  const columns = [...new Set(data.flatMap((row) => Object.keys(row)))].filter((column) =>
    data.some((row) => typeof row[column] !== 'object' || row[column] === null),
  )
  return { columns, rows: data }
})

const pairs = computed(() => (isRow(props.data) ? Object.entries(props.data) : null))

function cell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}
</script>

<template>
  <div v-if="table" class="overflow-x-auto rounded-lg border border-line">
    <table class="w-full text-sm">
      <thead class="table-head">
        <tr>
          <th v-for="column in table.columns" :key="column" class="px-3 py-2 font-medium">
            {{ column }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-line">
        <tr v-for="(row, index) in table.rows" :key="index">
          <td
            v-for="column in table.columns"
            :key="column"
            class="px-3 py-1.5 font-mono text-xs whitespace-nowrap"
          >
            {{ cell(row[column]) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <dl
    v-else-if="pairs"
    class="grid grid-cols-[minmax(0,auto)_1fr] gap-x-6 gap-y-1.5 rounded-lg border border-line p-3 text-sm"
  >
    <template v-for="[name, value] in pairs" :key="name">
      <dt class="text-muted">{{ name }}</dt>
      <dd class="font-mono text-xs break-all">{{ cell(value) }}</dd>
    </template>
  </dl>
  <p v-else-if="Array.isArray(data) && data.length === 0" class="text-sm text-muted">Sin filas.</p>
  <p v-else class="font-mono text-sm">{{ cell(data) }}</p>
</template>
