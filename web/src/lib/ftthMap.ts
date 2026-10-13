import type { MapBox, MapData } from '@/api'

/** Estado de una caja para pintarla y para la leyenda (nunca solo color: va con su texto). */
export type BoxState = 'down' | 'full' | 'free' | 'passive'

export const BOX_STATE: Record<BoxState, string> = {
  down: 'Con clientes caídos',
  full: 'Sin puertos libres',
  free: 'Con puertos libres',
  passive: 'Empalme u ODF',
}

export function boxState(box: Pick<MapBox, 'kind' | 'ports' | 'used' | 'down'>): BoxState {
  if (box.down > 0) return 'down'
  if (box.kind !== 'nap' || box.ports === 0) return 'passive'
  return box.used >= box.ports ? 'full' : 'free'
}

export const BOX_KIND: Record<MapBox['kind'], string> = {
  nap: 'Caja NAP',
  ceo: 'Empalme (CEO)',
  odf: 'ODF',
}

export function formatMeters(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)} m`
  return `${(meters / 1000).toLocaleString('es-CO', { maximumFractionDigits: 2 })} km`
}

/** Polígono aproximado de un círculo de ``radius`` metros (para dibujar la factibilidad). */
export function circle(lat: number, lng: number, radius: number, steps = 64): [number, number][] {
  const points: [number, number][] = []
  const dLat = radius / 111_320
  const dLng = radius / (111_320 * Math.cos((lat * Math.PI) / 180))
  for (let i = 0; i <= steps; i += 1) {
    const angle = (i / steps) * 2 * Math.PI
    points.push([lng + dLng * Math.cos(angle), lat + dLat * Math.sin(angle)])
  }
  return points
}

/** Caja que encierra todo lo dibujado, o null si el mapa está vacío. */
export function bounds(data: MapData): [[number, number], [number, number]] | null {
  const points: [number, number][] = [
    ...data.olts.flatMap((o) => (o.lat !== null && o.lng !== null ? [[o.lng, o.lat]] : [])),
    ...data.boxes.map((b) => [b.lng, b.lat] as [number, number]),
    ...data.poles.map((p) => [p.lng, p.lat] as [number, number]),
    ...data.cables.flatMap((c) => c.coords),
  ] as [number, number][]
  if (points.length === 0) return null
  const lngs = points.map((p) => p[0])
  const lats = points.map((p) => p[1])
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ]
}

/** El siguiente nombre libre: NAP-01, NAP-02… */
export function nextName(prefix: string, taken: string[]): string {
  for (let n = 1; ; n += 1) {
    const name = `${prefix}-${String(n).padStart(2, '0')}`
    if (!taken.includes(name)) return name
  }
}
