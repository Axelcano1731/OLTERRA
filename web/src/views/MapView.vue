<script setup lang="ts">
import {
  Box,
  Cable,
  Hand,
  LoaderCircle,
  MapPin,
  Radar,
  Server,
  Trash2,
  TowerControl,
  Undo2,
  X,
} from '@lucide/vue'
import {
  Map as MapLibre,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature } from 'geojson'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  shallowRef,
  useTemplateRef,
} from 'vue'

import {
  assignPort,
  createBox,
  createCable,
  createPole,
  deleteBox,
  deleteCable,
  deletePole,
  feasibility,
  getBox,
  getMap,
  getMapConfig,
  listOnuStatus,
  placeOlt,
  releasePort,
  updateBox,
  type BoxKind,
  type Feasibility,
  type MapBoxDetail,
  type MapData,
  type OnuStatus,
  type Splitter,
} from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import {
  BOX_KIND,
  BOX_STATE,
  boxState,
  bounds,
  circle,
  formatMeters,
  nextName,
} from '@/lib/ftthMap'
import { formatDbm } from '@/lib/monitoring'
import { errorText } from '@/lib/useAsync'
import { can } from '@/session'

setWorkerUrl(workerUrl)

type Mode = 'view' | 'box' | 'pole' | 'cable' | 'feasibility' | 'olt'
type Selection =
  | { type: 'box'; id: string }
  | { type: 'cable'; id: string }
  | { type: 'pole'; id: string }
  | { type: 'olt'; id: string }
  | null

const canEdit = can('map:write')
const container = useTemplateRef<HTMLDivElement>('container')
let map: MapLibre | null = null

const data = shallowRef<MapData | null>(null)
const mode = ref<Mode>('view')
const selection = ref<Selection>(null)
const box = shallowRef<MapBoxDetail | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
const ready = ref(false)

// Formularios
interface BoxForm {
  name: string
  kind: BoxKind
  ports: number
  splitter: Splitter | ''
  oltId: string
  pon: number | null
}

const draftBox = reactive<BoxForm & { open: boolean; lat: number; lng: number }>({
  open: false,
  lat: 0,
  lng: 0,
  name: '',
  kind: 'nap',
  ports: 8,
  splitter: '1:8',
  oltId: '',
  pon: null,
})
const draftCable = reactive({ points: [] as [number, number][], name: '', fibers: 12 })
const oltToPlace = ref('')
const radius = ref(300)
const feasible = shallowRef<Feasibility | null>(null)
const editingBox = ref(false)
const boxEdit = reactive<BoxForm>({
  name: '',
  kind: 'nap',
  ports: 8,
  splitter: '',
  oltId: '',
  pon: null,
})

// Asignar cliente a un puerto
const assigning = ref<number | null>(null)
const onuSearch = ref('')
const onuOltId = ref('')
const onuOptions = shallowRef<OnuStatus[]>([])

const selectedCable = computed(() =>
  selection.value?.type === 'cable'
    ? data.value?.cables.find((c) => c.id === selection.value?.id)
    : undefined,
)
const selectedPole = computed(() =>
  selection.value?.type === 'pole'
    ? data.value?.poles.find((p) => p.id === selection.value?.id)
    : undefined,
)
const selectedOlt = computed(() =>
  selection.value?.type === 'olt'
    ? data.value?.olts.find((o) => o.id === selection.value?.id)
    : undefined,
)
const unplaced = computed(() => (data.value?.olts ?? []).filter((o) => o.lat === null))
const oltName = (id: string | null) => data.value?.olts.find((o) => o.id === id)?.name ?? '—'

const filteredOnus = computed(() => {
  const term = onuSearch.value.trim().toLowerCase()
  return onuOptions.value
    .filter((o) => !term || `${o.description ?? ''} ${o.serial ?? ''}`.toLowerCase().includes(term))
    .slice(0, 30)
})

// --- Colores: los tokens del tema (la leyenda dice qué es cada uno) ---------------------------

function token(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

// --- Fuentes GeoJSON --------------------------------------------------------------------------

function setSource(id: string, features: Feature[]): void {
  const source = map?.getSource<GeoJSONSource>(id)
  void source?.setData({ type: 'FeatureCollection', features })
}

function paint(): void {
  const current = data.value
  if (!map || !current) return
  setSource(
    'cables',
    current.cables.map((c) => ({
      type: 'Feature',
      properties: { id: c.id, name: c.name },
      geometry: { type: 'LineString', coordinates: c.coords },
    })),
  )
  setSource(
    'poles',
    current.poles.map((p) => ({
      type: 'Feature',
      properties: { id: p.id },
      geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
    })),
  )
  setSource(
    'boxes',
    current.boxes.map((b) => ({
      type: 'Feature',
      properties: { id: b.id, name: b.name, state: boxState(b) },
      geometry: { type: 'Point', coordinates: [b.lng, b.lat] },
    })),
  )
  setSource(
    'olts',
    current.olts.flatMap((o) =>
      o.lat === null || o.lng === null
        ? []
        : [
            {
              type: 'Feature' as const,
              properties: { id: o.id, name: o.name },
              geometry: { type: 'Point' as const, coordinates: [o.lng, o.lat] },
            },
          ],
    ),
  )
  map.setFilter('selected', ['==', ['get', 'id'], selection.value?.id ?? ''])
}

function paintDraft(): void {
  const points = draftCable.points
  setSource('draft', [
    ...(points.length >= 2
      ? [
          {
            type: 'Feature' as const,
            properties: {},
            geometry: { type: 'LineString' as const, coordinates: points },
          },
        ]
      : []),
    ...points.map((p) => ({
      type: 'Feature' as const,
      properties: {},
      geometry: { type: 'Point' as const, coordinates: p },
    })),
  ])
}

function paintFeasibility(lat?: number, lng?: number): void {
  if (lat === undefined || lng === undefined) {
    setSource('feasibility', [])
    return
  }
  setSource('feasibility', [
    {
      type: 'Feature',
      properties: {},
      geometry: { type: 'Polygon', coordinates: [circle(lat, lng, radius.value)] },
    },
    { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [lng, lat] } },
  ])
}

async function reload(): Promise<void> {
  try {
    data.value = await getMap()
    paint()
  } catch (caught) {
    error.value = errorText(caught)
  }
}

// --- Mapa -------------------------------------------------------------------------------------

onMounted(async () => {
  try {
    const [config, initial] = await Promise.all([getMapConfig(), getMap()])
    data.value = initial
    if (!container.value) return
    const area = bounds(initial)
    map = new MapLibre({
      container: container.value,
      style: config.style_url,
      center: [-74.08, 4.6],
      zoom: area ? 14 : 5,
      attributionControl: { compact: true },
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-right')
    map.on('load', () => {
      addLayers()
      paint()
      if (area) map?.fitBounds(area, { padding: 60, maxZoom: 17, duration: 0 })
      ready.value = true
    })
    map.on('click', (event) => void onClick(event))
    map.on('dblclick', (event) => {
      if (mode.value === 'cable') {
        event.preventDefault()
        finishCable()
      }
    })
  } catch (caught) {
    error.value = errorText(caught)
  }
})
onBeforeUnmount(() => map?.remove())

function addLayers(): void {
  if (!map) return
  const accent = token('--color-chart-line', '#4f5bd5')
  const surface = token('--color-surface', '#ffffff')
  const ink = token('--color-ink', '#0f1a2e')
  const muted = token('--color-muted', '#5b6b85')
  const colors = {
    down: token('--color-danger', '#c62828'),
    full: token('--color-warning', '#b45309'),
    free: token('--color-success', '#047857'),
    passive: muted,
  }
  const empty = { type: 'FeatureCollection' as const, features: [] }
  for (const id of ['cables', 'poles', 'boxes', 'olts', 'draft', 'feasibility']) {
    map.addSource(id, { type: 'geojson', data: empty })
  }
  const label = {
    'text-font': ['Noto Sans Regular'],
    'text-size': 11,
    'text-offset': [0, 1.3] as [number, number],
    'text-anchor': 'top' as const,
  }
  const halo = { 'text-color': ink, 'text-halo-color': surface, 'text-halo-width': 1.5 }
  map.addLayer({
    id: 'feasibility-fill',
    type: 'fill',
    source: 'feasibility',
    filter: ['==', '$type', 'Polygon'],
    paint: { 'fill-color': accent, 'fill-opacity': 0.1 },
  })
  map.addLayer({
    id: 'feasibility-line',
    type: 'line',
    source: 'feasibility',
    filter: ['==', '$type', 'Polygon'],
    paint: { 'line-color': accent, 'line-width': 1 },
  })
  map.addLayer({
    id: 'cables-line',
    type: 'line',
    source: 'cables',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': accent, 'line-width': 3 },
  })
  map.addLayer({
    id: 'cables-label',
    type: 'symbol',
    source: 'cables',
    layout: { 'symbol-placement': 'line', 'text-field': ['get', 'name'], ...label },
    paint: halo,
  })
  map.addLayer({
    id: 'draft-line',
    type: 'line',
    source: 'draft',
    filter: ['==', '$type', 'LineString'],
    paint: { 'line-color': accent, 'line-width': 2, 'line-dasharray': [2, 1] },
  })
  map.addLayer({
    id: 'draft-points',
    type: 'circle',
    source: 'draft',
    filter: ['==', '$type', 'Point'],
    paint: {
      'circle-radius': 4,
      'circle-color': accent,
      'circle-stroke-color': surface,
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'poles-circle',
    type: 'circle',
    source: 'poles',
    paint: {
      'circle-radius': 4,
      'circle-color': muted,
      'circle-stroke-color': surface,
      'circle-stroke-width': 1.5,
    },
  })
  map.addLayer({
    id: 'boxes-circle',
    type: 'circle',
    source: 'boxes',
    paint: {
      'circle-radius': 7,
      'circle-color': [
        'match',
        ['get', 'state'],
        'down',
        colors.down,
        'full',
        colors.full,
        'free',
        colors.free,
        colors.passive,
      ],
      'circle-stroke-color': surface,
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'boxes-label',
    type: 'symbol',
    source: 'boxes',
    minzoom: 14,
    layout: { 'text-field': ['get', 'name'], ...label },
    paint: halo,
  })
  map.addLayer({
    id: 'olts-circle',
    type: 'circle',
    source: 'olts',
    paint: {
      'circle-radius': 9,
      'circle-color': ink,
      'circle-stroke-color': surface,
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'olts-label',
    type: 'symbol',
    source: 'olts',
    layout: {
      'text-field': ['get', 'name'],
      ...label,
      'text-offset': [0, 1.5] as [number, number],
    },
    paint: halo,
  })
  map.addLayer({
    id: 'feasibility-point',
    type: 'circle',
    source: 'feasibility',
    filter: ['==', '$type', 'Point'],
    paint: {
      'circle-radius': 6,
      'circle-color': accent,
      'circle-stroke-color': surface,
      'circle-stroke-width': 2,
    },
  })
  for (const source of ['boxes', 'olts', 'poles']) {
    map.addLayer({
      id: source === 'boxes' ? 'selected' : `selected-${source}`,
      type: 'circle',
      source,
      filter: ['==', ['get', 'id'], ''],
      paint: {
        'circle-radius': 13,
        'circle-opacity': 0,
        'circle-stroke-color': accent,
        'circle-stroke-width': 3,
      },
    })
  }
  for (const layer of ['boxes-circle', 'olts-circle', 'poles-circle', 'cables-line']) {
    map.on('mouseenter', layer, () => {
      if (map && mode.value === 'view') map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', layer, () => {
      if (map) map.getCanvas().style.cursor = ''
    })
  }
}

function setSelected(next: Selection): void {
  selection.value = next
  for (const layer of ['selected', 'selected-olts', 'selected-poles']) {
    map?.setFilter(layer, ['==', ['get', 'id'], next?.id ?? ''])
  }
}

async function onClick(event: MapMouseEvent): Promise<void> {
  if (!map) return
  const { lat, lng } = event.lngLat
  error.value = null
  if (mode.value === 'box') {
    Object.assign(draftBox, {
      open: true,
      lat,
      lng,
      name: nextName('NAP', data.value?.boxes.map((b) => b.name) ?? []),
    })
    return
  }
  if (mode.value === 'pole') {
    await run(async () => {
      const pole = await createPole({ lat, lng, name: null, owner: null, notes: null })
      await reload()
      setSelected({ type: 'pole', id: pole.id })
    })
    return
  }
  if (mode.value === 'cable') {
    draftCable.points = [...draftCable.points, [lng, lat]]
    paintDraft()
    return
  }
  if (mode.value === 'feasibility') {
    paintFeasibility(lat, lng)
    await run(async () => {
      feasible.value = await feasibility(lat, lng, radius.value)
    })
    return
  }
  if (mode.value === 'olt') {
    if (!oltToPlace.value) {
      error.value = 'Elige primero qué OLT vas a ubicar.'
      return
    }
    const id = oltToPlace.value
    await run(async () => {
      await placeOlt(id, lat, lng)
      oltToPlace.value = ''
      setMode('view')
      await reload()
      setSelected({ type: 'olt', id })
    })
    return
  }
  const [hit] = map.queryRenderedFeatures(event.point, {
    layers: ['boxes-circle', 'olts-circle', 'poles-circle', 'cables-line'],
  })
  const id = hit?.properties?.id as string | undefined
  if (!hit || !id) {
    setSelected(null)
    box.value = null
    return
  }
  const layer = hit.layer.id
  if (layer === 'boxes-circle') await selectBox(id)
  else if (layer === 'olts-circle') setSelected({ type: 'olt', id })
  else if (layer === 'poles-circle') setSelected({ type: 'pole', id })
  else setSelected({ type: 'cable', id })
}

async function run(action: () => Promise<void>): Promise<void> {
  busy.value = true
  try {
    await action()
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

function setMode(next: Mode): void {
  mode.value = next
  draftBox.open = false
  draftCable.points = []
  paintDraft()
  if (next !== 'feasibility') {
    feasible.value = null
    paintFeasibility()
  }
  if (next !== 'view') {
    setSelected(null)
    box.value = null
  }
  if (map) map.getCanvas().style.cursor = next === 'view' ? '' : 'crosshair'
  if (next === 'cable') map?.doubleClickZoom.disable()
  else map?.doubleClickZoom.enable()
}

// --- Cajas ------------------------------------------------------------------------------------

async function selectBox(id: string): Promise<void> {
  setSelected({ type: 'box', id })
  editingBox.value = false
  assigning.value = null
  await run(async () => {
    box.value = await getBox(id)
  })
}

async function saveDraftBox(): Promise<void> {
  await run(async () => {
    const created = await createBox({
      kind: draftBox.kind,
      name: draftBox.name.trim(),
      lat: draftBox.lat,
      lng: draftBox.lng,
      ports: draftBox.kind === 'nap' ? draftBox.ports : 0,
      splitter: draftBox.splitter || null,
      olt_id: draftBox.oltId || null,
      pon: draftBox.pon,
    })
    draftBox.open = false
    setMode('view')
    await reload()
    await selectBox(created.id)
  })
}

function startEditBox(): void {
  const current = box.value
  if (!current) return
  Object.assign(boxEdit, {
    name: current.name,
    kind: current.kind,
    ports: current.ports,
    splitter: (current.splitter ?? '') as Splitter | '',
    oltId: current.olt_id ?? '',
    pon: current.pon,
  })
  editingBox.value = true
}

async function saveBox(): Promise<void> {
  const current = box.value
  if (!current) return
  await run(async () => {
    box.value = await updateBox(current.id, {
      name: boxEdit.name.trim(),
      kind: boxEdit.kind,
      ports: boxEdit.ports,
      splitter: boxEdit.splitter || null,
      olt_id: boxEdit.oltId || null,
      pon: boxEdit.pon,
    })
    editingBox.value = false
    await reload()
  })
}

async function removeBox(): Promise<void> {
  const current = box.value
  if (!current || !window.confirm(`¿Eliminar ${current.name}? Sus puertos quedan libres.`)) return
  await run(async () => {
    await deleteBox(current.id)
    box.value = null
    setSelected(null)
    await reload()
  })
}

async function startAssign(port: number): Promise<void> {
  assigning.value = port
  onuSearch.value = ''
  onuOltId.value = box.value?.olt_id ?? data.value?.olts[0]?.id ?? ''
  await loadOnuOptions()
}

async function loadOnuOptions(): Promise<void> {
  onuOptions.value = onuOltId.value ? await listOnuStatus({ olt_id: onuOltId.value }) : []
}

async function assign(onu: OnuStatus): Promise<void> {
  const current = box.value
  if (!current || assigning.value === null) return
  const port = assigning.value
  await run(async () => {
    box.value = await assignPort(current.id, port, {
      olt_id: onu.olt_id,
      pon: onu.pon,
      onu: onu.onu,
    })
    assigning.value = null
    await reload()
  })
}

async function release(port: number): Promise<void> {
  const current = box.value
  if (!current) return
  await run(async () => {
    box.value = await releasePort(current.id, port)
    await reload()
  })
}

const portRows = computed(() => {
  const current = box.value
  if (!current) return []
  const byPort = new Map(current.port_list.map((p) => [p.port, p]))
  return Array.from({ length: current.ports }, (_, i) => ({ port: i + 1, onu: byPort.get(i + 1) }))
})

// --- Cables y postes --------------------------------------------------------------------------

function undoPoint(): void {
  draftCable.points = draftCable.points.slice(0, -1)
  paintDraft()
}

function finishCable(): void {
  if (draftCable.points.length < 2) return
  if (!draftCable.name)
    draftCable.name = nextName('CABLE', data.value?.cables.map((c) => c.name) ?? [])
}

async function saveCable(): Promise<void> {
  await run(async () => {
    const created = await createCable({
      name: draftCable.name.trim(),
      fibers: draftCable.fibers,
      coords: draftCable.points,
      notes: null,
    })
    setMode('view')
    await reload()
    setSelected({ type: 'cable', id: created.id })
  })
}

async function removeCable(id: string): Promise<void> {
  if (!window.confirm('¿Eliminar este cable?')) return
  await run(async () => {
    await deleteCable(id)
    setSelected(null)
    await reload()
  })
}

async function removePole(id: string): Promise<void> {
  await run(async () => {
    await deletePole(id)
    setSelected(null)
    await reload()
  })
}

function flyTo(lat: number, lng: number): void {
  map?.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), 17) })
}

const MODES: { value: Mode; label: string; icon: unknown; edit: boolean }[] = [
  { value: 'view', label: 'Ver', icon: Hand, edit: false },
  { value: 'box', label: 'Caja', icon: Box, edit: true },
  { value: 'cable', label: 'Cable', icon: Cable, edit: true },
  { value: 'pole', label: 'Poste', icon: TowerControl, edit: true },
  { value: 'olt', label: 'Ubicar OLT', icon: Server, edit: true },
  { value: 'feasibility', label: 'Factibilidad', icon: Radar, edit: false },
]

const HINTS: Record<Mode, string> = {
  view: 'Toca una caja, un cable, un poste o la OLT para ver su detalle.',
  box: 'Toca el mapa donde está la caja.',
  cable: 'Toca cada punto del recorrido; doble toque o «Terminar» para cerrarlo.',
  pole: 'Toca el mapa donde está cada poste.',
  olt: 'Elige la OLT y toca el mapa donde está.',
  feasibility: 'Toca la ubicación del prospecto: Olterra busca cajas con puertos libres cerca.',
}

const LEGEND = (['down', 'full', 'free', 'passive'] as const).map((state) => ({
  state,
  label: BOX_STATE[state],
  tone: { down: 'bg-danger', full: 'bg-warning', free: 'bg-success', passive: 'bg-muted' }[state],
}))
</script>

<template>
  <PageHeader
    title="Mapa FTTH"
    description="Cajas, cables y postes de tu red, con el estado de cada cliente según el monitoreo."
  />

  <AlertBox v-if="error" tone="danger" class="mb-4">{{ error }}</AlertBox>

  <div class="mb-3 flex flex-wrap items-center gap-2">
    <div class="flex flex-wrap rounded-lg border border-line bg-surface p-0.5 text-sm" role="group">
      <button
        v-for="option in MODES.filter((m) => !m.edit || canEdit)"
        :key="option.value"
        type="button"
        :aria-pressed="mode === option.value"
        :class="[
          'flex items-center gap-1.5 rounded-md px-3 py-1.5',
          mode === option.value ? 'bg-accent text-on-accent' : 'text-muted hover:text-ink',
        ]"
        @click="setMode(option.value)"
      >
        <component :is="option.icon" class="size-4" />
        {{ option.label }}
      </button>
    </div>
    <p class="text-sm text-muted">{{ HINTS[mode] }}</p>
    <LoaderCircle v-if="busy" class="size-4 animate-spin text-muted" />
  </div>

  <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
    <div class="card relative h-[calc(100dvh-15rem)] min-h-[28rem] overflow-hidden p-0">
      <!-- MapLibre le pone position: relative al contenedor: el alto va explícito, no con inset-0. -->
      <div ref="container" class="h-full w-full" />
      <div
        v-if="ready"
        class="pointer-events-none absolute bottom-3 left-3 rounded-lg border border-line bg-surface/95 px-3 py-2 text-xs shadow-sm"
      >
        <p v-for="item in LEGEND" :key="item.state" class="flex items-center gap-2">
          <span :class="['inline-block size-2.5 rounded-full', item.tone]" /> {{ item.label }}
        </p>
        <p class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-full bg-ink" /> OLT
        </p>
        <p class="flex items-center gap-2">
          <span class="inline-block h-0.5 w-3 rounded bg-[var(--color-chart-line)]" /> Cable
        </p>
        <p class="flex items-center gap-2">
          <span class="inline-block size-2 rounded-full bg-muted" /> Poste
        </p>
      </div>
    </div>

    <aside class="space-y-4">
      <!-- OLT sin ubicar -->
      <section v-if="canEdit && unplaced.length && mode !== 'olt'" class="card p-4 text-sm">
        <p class="flex items-center gap-1.5 font-medium">
          <MapPin class="size-4 text-muted" /> Sin ubicar en el mapa
        </p>
        <p class="mt-1 text-muted">{{ unplaced.map((o) => o.name).join(', ') }}</p>
        <button type="button" class="btn-secondary mt-2" @click="setMode('olt')">Ubicar OLT</button>
      </section>

      <!-- Ubicar OLT -->
      <section v-if="mode === 'olt'" class="card p-4">
        <label for="olt-to-place" class="label">OLT</label>
        <select id="olt-to-place" v-model="oltToPlace" class="input">
          <option value="" disabled>Elige una OLT</option>
          <option v-for="olt in data?.olts ?? []" :key="olt.id" :value="olt.id">
            {{ olt.name }}{{ olt.lat === null ? ' (sin ubicar)' : '' }}
          </option>
        </select>
        <p class="hint">Luego toca el mapa donde está.</p>
      </section>

      <!-- Caja nueva -->
      <form v-if="draftBox.open" class="card space-y-3 p-4" @submit.prevent="saveDraftBox">
        <h2 class="font-semibold">Caja nueva</h2>
        <div>
          <label for="box-name" class="label">Nombre</label>
          <input id="box-name" v-model="draftBox.name" class="input" maxlength="60" required />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label for="box-kind" class="label">Tipo</label>
            <select id="box-kind" v-model="draftBox.kind" class="input">
              <option v-for="(label, kind) in BOX_KIND" :key="kind" :value="kind">
                {{ label }}
              </option>
            </select>
          </div>
          <div v-if="draftBox.kind === 'nap'">
            <label for="box-ports" class="label">Puertos</label>
            <input
              id="box-ports"
              v-model.number="draftBox.ports"
              type="number"
              min="1"
              max="144"
              class="input"
            />
          </div>
          <div>
            <label for="box-splitter" class="label">Splitter</label>
            <select id="box-splitter" v-model="draftBox.splitter" class="input">
              <option value="">Sin splitter</option>
              <option v-for="s in ['1:2', '1:4', '1:8', '1:16', '1:32', '1:64']" :key="s">
                {{ s }}
              </option>
            </select>
          </div>
          <div>
            <label for="box-pon" class="label">PON</label>
            <input
              id="box-pon"
              v-model.number="draftBox.pon"
              type="number"
              min="1"
              max="16"
              class="input"
            />
          </div>
        </div>
        <div>
          <label for="box-olt" class="label">Sale de la OLT</label>
          <select id="box-olt" v-model="draftBox.oltId" class="input">
            <option value="">Sin indicar</option>
            <option v-for="olt in data?.olts ?? []" :key="olt.id" :value="olt.id">
              {{ olt.name }}
            </option>
          </select>
        </div>
        <div class="flex justify-end gap-2">
          <button type="button" class="btn-secondary" @click="draftBox.open = false">
            Cancelar
          </button>
          <button type="submit" class="btn-primary" :disabled="busy">Guardar caja</button>
        </div>
      </form>

      <!-- Cable en curso -->
      <section v-if="mode === 'cable'" class="card space-y-3 p-4">
        <h2 class="font-semibold">Cable nuevo</h2>
        <p class="text-sm text-muted">{{ draftCable.points.length }} puntos.</p>
        <div class="flex gap-2">
          <button
            type="button"
            class="btn-secondary"
            :disabled="!draftCable.points.length"
            @click="undoPoint"
          >
            <Undo2 class="size-4" /> Deshacer punto
          </button>
          <button
            type="button"
            class="btn-secondary"
            :disabled="draftCable.points.length < 2"
            @click="finishCable"
          >
            Terminar
          </button>
        </div>
        <form v-if="draftCable.name" class="space-y-3" @submit.prevent="saveCable">
          <div class="grid grid-cols-[minmax(0,1fr)_6rem] gap-3">
            <div>
              <label for="cable-name" class="label">Nombre</label>
              <input id="cable-name" v-model="draftCable.name" class="input" maxlength="60" />
            </div>
            <div>
              <label for="cable-fibers" class="label">Hilos</label>
              <input
                id="cable-fibers"
                v-model.number="draftCable.fibers"
                type="number"
                min="1"
                max="288"
                class="input"
              />
            </div>
          </div>
          <button type="submit" class="btn-primary w-full" :disabled="busy">Guardar cable</button>
        </form>
      </section>

      <!-- Factibilidad -->
      <section v-if="mode === 'feasibility'" class="card space-y-3 p-4">
        <div>
          <label for="radius" class="label">Radio de búsqueda</label>
          <select id="radius" v-model.number="radius" class="input">
            <option :value="150">150 m</option>
            <option :value="300">300 m</option>
            <option :value="500">500 m</option>
            <option :value="1000">1 km</option>
          </select>
        </div>
        <template v-if="feasible">
          <p v-if="feasible.options.length === 0" class="text-sm">
            <StatusPill tone="danger">Sin cobertura</StatusPill>
            No hay cajas con puertos libres a menos de {{ formatMeters(feasible.radius_m) }}.
          </p>
          <template v-else>
            <p class="text-sm">
              <StatusPill tone="success">Hay cobertura</StatusPill>
            </p>
            <ul class="divide-y divide-line text-sm">
              <li v-for="option in feasible.options" :key="option.box.id">
                <button
                  type="button"
                  class="flex w-full items-center justify-between gap-2 py-2 text-left hover:text-accent"
                  @click="flyTo(option.box.lat, option.box.lng)"
                >
                  <span class="font-medium">{{ option.box.name }}</span>
                  <span class="text-xs text-muted">
                    {{ formatMeters(option.distance_m) }} · {{ option.free_ports }} libre{{
                      option.free_ports === 1 ? '' : 's'
                    }}
                  </span>
                </button>
              </li>
            </ul>
          </template>
        </template>
      </section>

      <!-- Caja seleccionada -->
      <section v-if="box && selection?.type === 'box'" class="card">
        <div class="flex items-start gap-2 border-b border-line px-4 py-3">
          <div class="min-w-0 flex-1">
            <h2 class="font-semibold">{{ box.name }}</h2>
            <p class="text-xs text-muted">
              {{ BOX_KIND[box.kind] }}
              <template v-if="box.splitter"> · splitter {{ box.splitter }}</template>
              <template v-if="box.olt_id"> · {{ oltName(box.olt_id) }}</template>
              <template v-if="box.pon"> · PON {{ box.pon }}</template>
            </p>
          </div>
          <button
            type="button"
            class="btn-ghost p-1"
            aria-label="Cerrar"
            @click="setSelected(null)"
          >
            <X class="size-4" />
          </button>
        </div>
        <div class="space-y-3 px-4 py-3 text-sm">
          <p v-if="box.ports" class="flex flex-wrap gap-2">
            <StatusPill :tone="box.used >= box.ports ? 'warning' : 'success'">
              {{ box.used }}/{{ box.ports }} puertos ocupados
            </StatusPill>
            <StatusPill v-if="box.down" tone="danger">{{ box.down }} caídos</StatusPill>
          </p>

          <form v-if="editingBox" class="space-y-2" @submit.prevent="saveBox">
            <input v-model="boxEdit.name" class="input" maxlength="60" aria-label="Nombre" />
            <div class="grid grid-cols-2 gap-2">
              <select v-model="boxEdit.kind" class="input" aria-label="Tipo">
                <option v-for="(label, kind) in BOX_KIND" :key="kind" :value="kind">
                  {{ label }}
                </option>
              </select>
              <input
                v-model.number="boxEdit.ports"
                type="number"
                min="0"
                max="144"
                class="input"
                aria-label="Puertos"
              />
              <select v-model="boxEdit.splitter" class="input" aria-label="Splitter">
                <option value="">Sin splitter</option>
                <option v-for="s in ['1:2', '1:4', '1:8', '1:16', '1:32', '1:64']" :key="s">
                  {{ s }}
                </option>
              </select>
              <input
                v-model.number="boxEdit.pon"
                type="number"
                min="1"
                max="16"
                class="input"
                placeholder="PON"
                aria-label="PON"
              />
            </div>
            <select v-model="boxEdit.oltId" class="input" aria-label="OLT">
              <option value="">Sin OLT</option>
              <option v-for="olt in data?.olts ?? []" :key="olt.id" :value="olt.id">
                {{ olt.name }}
              </option>
            </select>
            <div class="flex justify-end gap-2">
              <button type="button" class="btn-secondary" @click="editingBox = false">
                Cancelar
              </button>
              <button type="submit" class="btn-primary" :disabled="busy">Guardar</button>
            </div>
          </form>

          <ol v-if="box.ports" class="divide-y divide-line rounded-lg border border-line">
            <li v-for="row in portRows" :key="row.port" class="px-3 py-2">
              <div class="flex items-center gap-2">
                <span class="w-6 text-xs text-muted tabular-nums">{{ row.port }}</span>
                <span class="min-w-0 flex-1 truncate">
                  <template v-if="row.onu">
                    {{ row.onu.description ?? `ONU ${row.onu.pon}/${row.onu.onu}` }}
                  </template>
                  <span v-else class="text-muted">Libre</span>
                </span>
                <template v-if="row.onu">
                  <StatusPill
                    v-if="row.onu.online !== null"
                    :tone="row.onu.online ? 'success' : 'danger'"
                  >
                    {{ row.onu.online ? formatDbm(row.onu.rx_dbm) : row.onu.phase_label }}
                  </StatusPill>
                  <button
                    v-if="canEdit"
                    type="button"
                    class="btn-ghost p-1"
                    :aria-label="`Liberar el puerto ${row.port}`"
                    @click="release(row.port)"
                  >
                    <X class="size-3.5" />
                  </button>
                </template>
                <button
                  v-else-if="canEdit"
                  type="button"
                  class="text-xs text-accent underline"
                  @click="startAssign(row.port)"
                >
                  Asignar
                </button>
              </div>
              <div v-if="assigning === row.port" class="mt-2 space-y-2">
                <select
                  v-model="onuOltId"
                  class="input"
                  aria-label="OLT del cliente"
                  @change="loadOnuOptions"
                >
                  <option v-for="olt in data?.olts ?? []" :key="olt.id" :value="olt.id">
                    {{ olt.name }}
                  </option>
                </select>
                <input
                  v-model="onuSearch"
                  type="search"
                  class="input"
                  placeholder="Buscar cliente o serial"
                  aria-label="Buscar cliente"
                />
                <p v-if="!onuOptions.length" class="text-xs text-muted">
                  El monitoreo todavía no tiene clientes de esa OLT.
                </p>
                <ul class="max-h-48 overflow-auto">
                  <li v-for="onu in filteredOnus" :key="`${onu.pon}-${onu.onu}`">
                    <button
                      type="button"
                      class="w-full rounded px-2 py-1 text-left text-xs hover:bg-subtle"
                      @click="assign(onu)"
                    >
                      {{ onu.description ?? onu.serial }}
                      <span class="text-muted">· PON {{ onu.pon }}/{{ onu.onu }}</span>
                    </button>
                  </li>
                </ul>
                <button
                  type="button"
                  class="text-xs text-muted underline"
                  @click="assigning = null"
                >
                  Cancelar
                </button>
              </div>
            </li>
          </ol>

          <div v-if="canEdit && !editingBox" class="flex justify-between gap-2">
            <button type="button" class="btn-secondary" @click="startEditBox">Editar</button>
            <button type="button" class="btn-ghost text-danger" @click="removeBox">
              <Trash2 class="size-4" /> Eliminar
            </button>
          </div>
        </div>
      </section>

      <!-- Cable seleccionado -->
      <section v-if="selectedCable" class="card space-y-2 p-4 text-sm">
        <h2 class="font-semibold">{{ selectedCable.name }}</h2>
        <p class="text-muted">
          {{ selectedCable.fibers }} hilos · {{ formatMeters(selectedCable.length_m) }} de recorrido
        </p>
        <button
          v-if="canEdit"
          type="button"
          class="btn-ghost text-danger"
          @click="removeCable(selectedCable.id)"
        >
          <Trash2 class="size-4" /> Eliminar
        </button>
      </section>

      <!-- Poste seleccionado -->
      <section v-if="selectedPole" class="card space-y-2 p-4 text-sm">
        <h2 class="font-semibold">{{ selectedPole.name ?? 'Poste' }}</h2>
        <p class="text-muted">Dueño: {{ selectedPole.owner ?? 'sin indicar' }}</p>
        <button
          v-if="canEdit"
          type="button"
          class="btn-ghost text-danger"
          @click="removePole(selectedPole.id)"
        >
          <Trash2 class="size-4" /> Eliminar
        </button>
      </section>

      <!-- OLT seleccionada -->
      <section v-if="selectedOlt" class="card space-y-2 p-4 text-sm">
        <h2 class="font-semibold">{{ selectedOlt.name }}</h2>
        <RouterLink :to="{ name: 'olt', params: { id: selectedOlt.id } }" class="underline">
          Ver la OLT
        </RouterLink>
      </section>

      <section
        v-if="mode === 'view' && !selection && data && !data.boxes.length"
        class="card p-4 text-sm text-muted"
      >
        Todavía no hay cajas. Ubica tu OLT y agrega las cajas NAP con «Caja»: después podrás poner
        cada cliente en su puerto y buscar factibilidad.
      </section>
    </aside>
  </div>
</template>
