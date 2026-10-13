<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'

import type { OnuSample } from '@/api'
import { formatDateTime } from '@/lib/format'
import { formatDbm, RX_LOW_CRITICAL, RX_LOW_WARNING } from '@/lib/monitoring'

const props = withDefaults(defineProps<{ samples: OnuSample[]; hours?: number }>(), {
  hours: 168,
})

const HEIGHT = 220
const M = { top: 12, right: 72, bottom: 28, left: 44 }

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

const points = computed(() =>
  props.samples.map((s) => ({ ...s, t: new Date(s.at).getTime() })).sort((a, b) => a.t - b.t),
)
const end = computed(() => Math.max(Date.now(), points.value.at(-1)?.t ?? 0))
const start = computed(() => end.value - props.hours * 3600_000)

const domain = computed(() => {
  const values = points.value.flatMap((p) => (p.rx_dbm === null ? [] : [p.rx_dbm]))
  const low = Math.min(-30, ...values.map((v) => v - 1))
  const high = Math.max(-10, ...values.map((v) => v + 1))
  return [Math.floor(low / 5) * 5, Math.ceil(high / 5) * 5] as const
})

const plotW = computed(() => width.value - M.left - M.right)
const plotH = HEIGHT - M.top - M.bottom
const x = (t: number) => M.left + ((t - start.value) / (end.value - start.value)) * plotW.value
const y = (dbm: number) =>
  M.top + ((domain.value[1] - dbm) / (domain.value[1] - domain.value[0])) * plotH

const yTicks = computed(() => {
  const ticks: number[] = []
  for (let v = domain.value[0]; v <= domain.value[1]; v += 5) ticks.push(v)
  return ticks
})

const xTicks = computed(() => {
  const step = props.hours > 48 ? 24 * 3600_000 : 6 * 3600_000
  const first = new Date(start.value)
  if (step >= 24 * 3600_000) first.setHours(24, 0, 0, 0)
  else first.setHours(Math.ceil(first.getHours() / 6) * 6, 0, 0, 0)
  const format = new Intl.DateTimeFormat(
    'es-CO',
    step >= 24 * 3600_000 ? { weekday: 'short', day: 'numeric' } : { hour: 'numeric' },
  )
  const ticks: { t: number; label: string }[] = []
  for (let t = first.getTime(); t <= end.value; t += step) {
    ticks.push({ t, label: format.format(new Date(t)) })
  }
  return ticks
})

/** La línea se corta donde la ONU estuvo caída o sin lectura. */
const path = computed(() => {
  let d = ''
  let open = false
  for (const p of points.value) {
    if (!p.online || p.rx_dbm === null) {
      open = false
      continue
    }
    d += `${open ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.rx_dbm).toFixed(1)}`
    open = true
  }
  return d
})

const outages = computed(() => points.value.filter((p) => !p.online))
const last = computed(() => {
  const p = points.value.at(-1)
  return p && p.online && p.rx_dbm !== null ? p : null
})

function nearest(px: number): number | null {
  if (!points.value.length) return null
  let best = 0
  for (let i = 1; i < points.value.length; i += 1) {
    const point = points.value[i]
    const winner = points.value[best]
    if (point && winner && Math.abs(x(point.t) - px) < Math.abs(x(winner.t) - px)) best = i
  }
  return best
}

function onMove(event: PointerEvent): void {
  const svg = event.currentTarget as SVGSVGElement
  const rect = svg.getBoundingClientRect()
  active.value = nearest(((event.clientX - rect.left) / rect.width) * width.value)
}

function onKey(event: KeyboardEvent): void {
  if (!points.value.length) return
  const current = active.value ?? points.value.length - 1
  if (event.key === 'ArrowLeft') active.value = Math.max(0, current - 1)
  else if (event.key === 'ArrowRight') active.value = Math.min(points.value.length - 1, current + 1)
  else return
  event.preventDefault()
}

const hovered = computed(() => (active.value === null ? null : points.value[active.value]))
const tooltipLeft = computed(() => {
  if (!hovered.value) return 0
  const px = x(hovered.value.t)
  return Math.min(Math.max(px - 80, 0), width.value - 180)
})
</script>

<template>
  <div>
    <div ref="box" class="relative">
      <p v-if="points.length === 0" class="py-10 text-center text-sm text-muted">
        Todavía no hay muestras de esta ONU.
      </p>
      <svg
        v-else
        :viewBox="`0 0 ${width} ${HEIGHT}`"
        :width="width"
        :height="HEIGHT"
        class="block max-w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-accent"
        role="img"
        tabindex="0"
        :aria-label="`Señal recibida de la ONU en las últimas ${hours} horas. Usa las flechas para recorrer las muestras.`"
        @pointermove="onMove"
        @pointerleave="active = null"
        @keydown="onKey"
        @blur="active = null"
      >
        <!-- Cuadrícula recesiva -->
        <g>
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
              {{ tick }}
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
        </g>

        <!-- Umbrales: color de estado con su etiqueta, nunca solo color -->
        <g v-for="limit in [RX_LOW_WARNING, RX_LOW_CRITICAL]" :key="limit">
          <line
            :x1="M.left"
            :x2="M.left + plotW"
            :y1="y(limit)"
            :y2="y(limit)"
            :stroke="limit === RX_LOW_CRITICAL ? 'var(--color-danger)' : 'var(--color-warning)'"
            stroke-width="1"
          />
          <text :x="M.left + plotW + 6" :y="y(limit) + 4" class="fill-muted text-[11px]">
            {{ limit === RX_LOW_CRITICAL ? 'Crítico' : 'Aviso' }} {{ limit }}
          </text>
        </g>

        <!-- Señal -->
        <path
          :d="path"
          fill="none"
          stroke="var(--color-chart-line)"
          stroke-width="2"
          stroke-linejoin="round"
          stroke-linecap="round"
        />

        <!-- Caídas: marcas sobre la base, con anillo de superficie -->
        <circle
          v-for="(p, index) in outages"
          :key="`caida-${index}`"
          :cx="x(p.t)"
          :cy="M.top + plotH"
          r="4"
          fill="var(--color-danger)"
          stroke="var(--color-surface)"
          stroke-width="2"
        />

        <!-- Último valor: punto final y etiqueta directa -->
        <template v-if="last && last.rx_dbm !== null && !hovered">
          <circle
            :cx="x(last.t)"
            :cy="y(last.rx_dbm)"
            r="4"
            fill="var(--color-chart-line)"
            stroke="var(--color-surface)"
            stroke-width="2"
          />
        </template>

        <!-- Cursor -->
        <template v-if="hovered">
          <line
            :x1="x(hovered.t)"
            :x2="x(hovered.t)"
            :y1="M.top"
            :y2="M.top + plotH"
            stroke="var(--color-muted)"
            stroke-width="1"
          />
          <circle
            :cx="x(hovered.t)"
            :cy="hovered.online && hovered.rx_dbm !== null ? y(hovered.rx_dbm) : M.top + plotH"
            r="5"
            :fill="
              hovered.online && hovered.rx_dbm !== null
                ? 'var(--color-chart-line)'
                : 'var(--color-danger)'
            "
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
        class="pointer-events-none absolute top-0 rounded-lg whitespace-nowrap border border-line bg-surface px-3 py-2 text-xs shadow-sm"
        :style="{ left: `${tooltipLeft}px` }"
        aria-live="polite"
      >
        <p class="text-sm font-semibold text-ink">
          {{ hovered.online ? formatDbm(hovered.rx_dbm) : 'Caída' }}
        </p>
        <p class="text-muted">{{ formatDateTime(hovered.at) }}</p>
      </div>
      <p
        v-if="last && last.rx_dbm !== null && !hovered"
        class="pointer-events-none absolute text-xs font-medium text-ink"
        :style="{ left: `${x(last.t) + 8}px`, top: `${y(last.rx_dbm) - 22}px` }"
      >
        {{ formatDbm(last.rx_dbm) }}
      </p>
    </div>

    <div v-if="points.length" class="mt-2 flex flex-wrap items-center gap-4 text-xs text-muted">
      <span class="flex items-center gap-1.5">
        <span class="inline-block h-0.5 w-4 rounded bg-[var(--color-chart-line)]" /> Señal (dBm)
      </span>
      <span class="flex items-center gap-1.5">
        <span class="inline-block size-2 rounded-full bg-danger" /> Caída
      </span>
      <button
        type="button"
        class="ml-auto underline hover:text-ink"
        @click="showTable = !showTable"
      >
        {{ showTable ? 'Ocultar tabla' : 'Ver como tabla' }}
      </button>
    </div>

    <div v-if="showTable" class="mt-2 max-h-64 overflow-auto rounded-lg border border-line">
      <table class="w-full text-left text-xs">
        <thead class="sticky top-0 bg-subtle text-muted">
          <tr>
            <th class="px-3 py-1.5 font-medium">Cuándo</th>
            <th class="px-3 py-1.5 font-medium">Estado</th>
            <th class="px-3 py-1.5 text-right font-medium">Señal</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-line">
          <tr v-for="p in [...points].reverse()" :key="p.t">
            <td class="px-3 py-1.5">{{ formatDateTime(p.at) }}</td>
            <td class="px-3 py-1.5">{{ p.online ? 'En línea' : 'Caída' }}</td>
            <td class="px-3 py-1.5 text-right tabular-nums">
              {{ p.online ? formatDbm(p.rx_dbm) : '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
