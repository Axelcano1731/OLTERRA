<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'

import { formatDateTime } from '@/lib/format'
import { formatBps, type RatePoint } from '@/lib/traffic'

// Velocidad en el tiempo, como MRTG: la bajada como área y la subida como línea, con la raya del
// plan del cliente si se conoce. Los huecos (la OLT no respondió, la ONU caída) cortan la línea.
const props = defineProps<{
  points: RatePoint[]
  /** Desde y hasta (ms): el eje no se ajusta a los datos, así se ven los huecos. */
  start: number
  end: number
  /** Más separados que esto, dos puntos no se unen. */
  gapMs: number
  planBps: number | null
  caption: string
}>()

const HEIGHT = 240
const M = { top: 14, right: 76, bottom: 28, left: 60 }

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

const active = ref<number | null>(null)
// Tope del eje pegado al dato (o al plan) y redondo: 15, 20, 25, 30, 40, 50 Mbps…
const top = computed(() => {
  const highest = Math.max(props.planBps ?? 0, ...props.points.map((p) => Math.max(p.down, p.up)))
  const wanted = highest * 1.08
  if (wanted <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(wanted))
  const factor = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((f) => f * power >= wanted) ?? 10
  return factor * power
})
const plotW = computed(() => width.value - M.left - M.right)
const plotH = HEIGHT - M.top - M.bottom
const x = (t: number) => M.left + ((t - props.start) / (props.end - props.start)) * plotW.value
const y = (bps: number) => M.top + plotH - (bps / top.value) * plotH
const base = M.top + plotH

const yTicks = computed(() => [0, 1, 2, 3, 4, 5].map((step) => (top.value * step) / 5))

const xTicks = computed(() => {
  const span = props.end - props.start
  const day = 24 * 3600_000
  const step = span > 10 * day ? 7 * day : span > 2 * day ? day : 3 * 3600_000
  const first = new Date(props.start)
  if (step >= day) first.setHours(24, 0, 0, 0)
  else first.setHours(Math.ceil(first.getHours() / 3) * 3, 0, 0, 0)
  const format = new Intl.DateTimeFormat(
    'es-CO',
    step >= day ? { weekday: 'short', day: 'numeric' } : { hour: 'numeric' },
  )
  const ticks: { t: number; label: string }[] = []
  for (let t = first.getTime(); t < props.end; t += step) {
    ticks.push({ t, label: format.format(new Date(t)) })
  }
  return ticks
})

/** Tramos sin huecos. */
const runs = computed(() => {
  const result: RatePoint[][] = []
  let current: RatePoint[] = []
  for (const point of props.points) {
    const previous = current.at(-1)
    if (previous && point.t - previous.t > props.gapMs) {
      result.push(current)
      current = []
    }
    current.push(point)
  }
  if (current.length) result.push(current)
  return result
})

function line(run: RatePoint[], key: 'down' | 'up'): string {
  return run.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join('')
}

function area(run: RatePoint[]): string {
  const first = run[0]
  const last = run.at(-1)
  if (!first || !last) return ''
  return `${line(run, 'down')}L${x(last.t).toFixed(1)},${base}L${x(first.t).toFixed(1)},${base}Z`
}

function onMove(event: PointerEvent): void {
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  const px = ((event.clientX - rect.left) / rect.width) * width.value
  let best: number | null = null
  props.points.forEach((point, index) => {
    const winner = best === null ? undefined : props.points[best]
    if (!winner || Math.abs(x(point.t) - px) < Math.abs(x(winner.t) - px)) best = index
  })
  active.value = best
}

function onKey(event: KeyboardEvent): void {
  if (!props.points.length) return
  const current = active.value ?? props.points.length - 1
  if (event.key === 'ArrowLeft') active.value = Math.max(0, current - 1)
  else if (event.key === 'ArrowRight') active.value = Math.min(props.points.length - 1, current + 1)
  else return
  event.preventDefault()
}

const hovered = computed(() => (active.value === null ? null : props.points[active.value]))
const tooltipLeft = computed(() =>
  hovered.value ? Math.min(Math.max(x(hovered.value.t) - 90, 0), width.value - 200) : 0,
)
</script>

<template>
  <div ref="box" class="relative">
    <p v-if="!points.length" class="py-10 text-center text-sm text-muted">
      Todavía no hay mediciones de velocidad en este periodo.
    </p>
    <svg
      v-else
      :viewBox="`0 0 ${width} ${HEIGHT}`"
      :width="width"
      :height="HEIGHT"
      class="block max-w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-accent"
      role="img"
      tabindex="0"
      :aria-label="`${caption}. Usa las flechas para recorrer las mediciones.`"
      @pointermove="onMove"
      @pointerleave="active = null"
      @keydown="onKey"
      @blur="active = null"
    >
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
          {{ tick === 0 ? '0' : formatBps(tick) }}
        </text>
      </g>
      <text
        v-for="tick in xTicks"
        :key="tick.t"
        :x="x(tick.t)"
        :y="HEIGHT - 8"
        text-anchor="middle"
        class="fill-muted text-[11px]"
      >
        {{ tick.label }}
      </text>

      <!-- Bajada: área con lavado y borde; subida: línea -->
      <template v-for="(run, index) in runs" :key="index">
        <path :d="area(run)" fill="var(--color-chart-down)" fill-opacity="0.14" />
        <path
          :d="line(run, 'down')"
          fill="none"
          stroke="var(--color-chart-down)"
          stroke-width="2"
          stroke-linejoin="round"
        />
        <path
          :d="line(run, 'up')"
          fill="none"
          stroke="var(--color-chart-up)"
          stroke-width="2"
          stroke-linejoin="round"
        />
      </template>

      <!-- El plan del cliente, rotulado -->
      <template v-if="planBps">
        <line
          :x1="M.left"
          :x2="M.left + plotW"
          :y1="y(planBps)"
          :y2="y(planBps)"
          stroke="var(--color-ink)"
          stroke-opacity="0.55"
          stroke-width="1"
        />
        <text :x="M.left + plotW + 6" :y="y(planBps) + 4" class="fill-ink text-[11px]">
          Plan {{ formatBps(planBps) }}
        </text>
      </template>

      <template v-if="hovered">
        <line
          :x1="x(hovered.t)"
          :x2="x(hovered.t)"
          :y1="M.top"
          :y2="base"
          stroke="var(--color-muted)"
          stroke-width="1"
        />
        <circle
          :cx="x(hovered.t)"
          :cy="y(hovered.down)"
          r="4"
          fill="var(--color-chart-down)"
          stroke="var(--color-surface)"
          stroke-width="2"
        />
        <circle
          :cx="x(hovered.t)"
          :cy="y(hovered.up)"
          r="4"
          fill="var(--color-chart-up)"
          stroke="var(--color-surface)"
          stroke-width="2"
        />
      </template>
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
      <p class="mb-1 text-muted">{{ formatDateTime(new Date(hovered.t).toISOString()) }}</p>
      <p class="flex items-center gap-2">
        <span class="inline-block h-0.5 w-3 rounded bg-[var(--color-chart-down)]" />
        <span class="text-sm font-semibold text-ink">{{ formatBps(hovered.down) }}</span>
        <span class="text-muted">bajada</span>
      </p>
      <p class="flex items-center gap-2">
        <span class="inline-block h-0.5 w-3 rounded bg-[var(--color-chart-up)]" />
        <span class="text-sm font-semibold text-ink">{{ formatBps(hovered.up) }}</span>
        <span class="text-muted">subida</span>
      </p>
    </div>
  </div>
</template>
