<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'

import { formatBps, formatBytes, niceCeiling, type TrafficBucket } from '@/lib/traffic'

const props = defineProps<{ buckets: TrafficBucket[]; caption: string }>()

const HEIGHT = 220
const M = { top: 12, right: 8, bottom: 28, left: 60 }
const BAR_MAX = 24
const GAP = 2 // hueco de superficie entre la bajada y la subida

const box = useTemplateRef<HTMLDivElement>('box')
const width = ref(640)
let observer: ResizeObserver | undefined
onMounted(() => {
  if (!box.value) return
  width.value = box.value.clientWidth || 640
  observer = new ResizeObserver(([entry]) => {
    if (entry) width.value = Math.max(280, entry.contentRect.width)
  })
  observer.observe(box.value)
})
onBeforeUnmount(() => observer?.disconnect())

const showTable = ref(false)
const active = ref<number | null>(null)

const empty = computed(() => props.buckets.every((b) => b.down === 0 && b.up === 0))
const top = computed(() => niceCeiling(Math.max(...props.buckets.flatMap((b) => [b.down, b.up]))))
const plotW = computed(() => width.value - M.left - M.right)
const plotH = HEIGHT - M.top - M.bottom
const slot = computed(() => plotW.value / Math.max(1, props.buckets.length))
const barW = computed(() => Math.max(2, Math.min(BAR_MAX, (slot.value * 0.7 - GAP) / 2)))

const y = (bytes: number) => M.top + plotH - (bytes / top.value) * plotH
const center = (index: number) => M.left + slot.value * (index + 0.5)

const yTicks = computed(() => [0, 1, 2, 3, 4, 5].map((step) => (top.value * step) / 5))

// Etiquetas del eje X sin chocar: una de cada tantas según el ancho.
const labelEvery = computed(() => Math.max(1, Math.ceil(52 / slot.value)))

/** Columna con la punta redondeada (4 px) y la base recta, sobre la línea base. */
function column(x: number, value: number): string {
  const height = (value / top.value) * plotH
  if (height <= 0) return ''
  const base = M.top + plotH
  const r = Math.min(4, barW.value / 2, height)
  const w = barW.value
  return (
    `M${x},${base}V${base - height + r}` +
    `Q${x},${base - height} ${x + r},${base - height}` +
    `H${x + w - r}Q${x + w},${base - height} ${x + w},${base - height + r}V${base}Z`
  )
}

function bars(index: number, bucket: TrafficBucket): { down: string; up: string } {
  const left = center(index) - barW.value - GAP / 2
  return {
    down: column(left, bucket.down),
    up: column(left + barW.value + GAP, bucket.up),
  }
}

function onMove(event: PointerEvent): void {
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  const px = ((event.clientX - rect.left) / rect.width) * width.value
  const index = Math.floor((px - M.left) / slot.value)
  active.value = index >= 0 && index < props.buckets.length ? index : null
}

function onKey(event: KeyboardEvent): void {
  if (!props.buckets.length) return
  const current = active.value ?? props.buckets.length - 1
  if (event.key === 'ArrowLeft') active.value = Math.max(0, current - 1)
  else if (event.key === 'ArrowRight')
    active.value = Math.min(props.buckets.length - 1, current + 1)
  else return
  event.preventDefault()
}

const hovered = computed(() => (active.value === null ? null : props.buckets[active.value]))
const tooltipLeft = computed(() => {
  if (active.value === null) return 0
  return Math.min(Math.max(center(active.value) - 90, 0), width.value - 200)
})
</script>

<template>
  <div>
    <div ref="box" class="relative">
      <p v-if="empty" class="py-10 text-center text-sm text-muted">
        Todavía no hay consumo medido en este periodo.
      </p>
      <svg
        v-else
        :viewBox="`0 0 ${width} ${HEIGHT}`"
        :width="width"
        :height="HEIGHT"
        class="block max-w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-accent"
        role="img"
        tabindex="0"
        :aria-label="`${caption}. Usa las flechas para recorrer las barras.`"
        @pointermove="onMove"
        @pointerleave="active = null"
        @keydown="onKey"
        @blur="active = null"
      >
        <!-- Cuadrícula recesiva -->
        <g v-for="tick in yTicks" :key="tick">
          <line
            :x1="M.left"
            :x2="M.left + plotW"
            :y1="y(tick)"
            :y2="y(tick)"
            stroke="var(--color-line)"
            stroke-width="1"
          />
          <text :x="M.left - 8" :y="y(tick) + 4" text-anchor="end" class="fill-muted text-[11px]">
            {{ tick === 0 ? '0' : formatBytes(tick) }}
          </text>
        </g>
        <template v-for="(bucket, index) in buckets" :key="bucket.start">
          <text
            v-if="index % labelEvery === (buckets.length - 1) % labelEvery"
            :x="center(index)"
            :y="HEIGHT - 8"
            text-anchor="middle"
            class="fill-muted text-[11px]"
          >
            {{ bucket.label }}
          </text>
        </template>

        <!-- Columnas: bajada y subida lado a lado, con un hueco de superficie -->
        <g
          v-for="(bucket, index) in buckets"
          :key="`b-${bucket.start}`"
          :opacity="active === null || active === index ? 1 : 0.45"
        >
          <path :d="bars(index, bucket).down" fill="var(--color-chart-down)" />
          <path :d="bars(index, bucket).up" fill="var(--color-chart-up)" />
        </g>

        <!-- Zona de captura: toda la columna del día u hora, no solo la barra -->
        <rect
          :x="M.left"
          :y="M.top"
          :width="plotW"
          :height="plotH"
          fill="transparent"
          pointer-events="all"
        />
      </svg>

      <div
        v-if="hovered"
        class="pointer-events-none absolute top-0 rounded-lg border border-line bg-surface px-3 py-2 text-xs whitespace-nowrap shadow-sm"
        :style="{ left: `${tooltipLeft}px` }"
        aria-live="polite"
      >
        <p class="mb-1 text-muted">{{ hovered.title }}</p>
        <p class="flex items-center gap-2">
          <span class="inline-block h-0.5 w-3 rounded bg-[var(--color-chart-down)]" />
          <span class="text-sm font-semibold text-ink">{{ formatBytes(hovered.down) }}</span>
          <span class="text-muted">bajada</span>
        </p>
        <p class="flex items-center gap-2">
          <span class="inline-block h-0.5 w-3 rounded bg-[var(--color-chart-up)]" />
          <span class="text-sm font-semibold text-ink">{{ formatBytes(hovered.up) }}</span>
          <span class="text-muted">subida</span>
        </p>
        <p v-if="hovered.peakDown" class="mt-1 text-muted">
          Pico de bajada: {{ formatBps(hovered.peakDown) }}
        </p>
      </div>
    </div>

    <div class="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted">
      <span class="flex items-center gap-1.5">
        <span class="inline-block size-2.5 rounded-sm bg-[var(--color-chart-down)]" /> Bajada
      </span>
      <span class="flex items-center gap-1.5">
        <span class="inline-block size-2.5 rounded-sm bg-[var(--color-chart-up)]" /> Subida
      </span>
      <button
        v-if="!empty"
        type="button"
        class="ml-auto underline hover:text-ink"
        @click="showTable = !showTable"
      >
        {{ showTable ? 'Ocultar tabla' : 'Ver como tabla' }}
      </button>
    </div>

    <div v-if="showTable" class="mt-2 max-h-64 overflow-auto rounded-lg border border-line">
      <table class="w-full text-left text-xs">
        <caption class="sr-only">
          {{
            caption
          }}
        </caption>
        <thead class="sticky top-0 bg-subtle text-muted">
          <tr>
            <th class="px-3 py-1.5 font-medium">Cuándo</th>
            <th class="px-3 py-1.5 text-right font-medium">Bajada</th>
            <th class="px-3 py-1.5 text-right font-medium">Subida</th>
            <th class="px-3 py-1.5 text-right font-medium">Pico de bajada</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr v-for="bucket in [...buckets].reverse()" :key="bucket.start">
            <td class="px-3 py-1.5">{{ bucket.title }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ formatBytes(bucket.down) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">{{ formatBytes(bucket.up) }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">
              {{ bucket.peakDown ? formatBps(bucket.peakDown) : '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
