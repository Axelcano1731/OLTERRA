import type { OnuTrafficHour } from '@/api'

const oneDecimal = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 })
const noDecimals = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })

/** "1,2 GB", "350 MB", "12 kB": unidades decimales, como las cuentan los planes de internet. */
export function formatBytes(bytes: number): string {
  if (bytes >= 1e12) return `${oneDecimal.format(bytes / 1e12)} TB`
  if (bytes >= 1e9) return `${oneDecimal.format(bytes / 1e9)} GB`
  if (bytes >= 1e6) return `${noDecimals.format(bytes / 1e6)} MB`
  if (bytes >= 1e3) return `${noDecimals.format(bytes / 1e3)} kB`
  return `${noDecimals.format(bytes)} B`
}

/** "3,4 Mbps", "850 kbps": una velocidad promedio. */
export function formatBps(bps: number | null | undefined): string {
  if (bps === null || bps === undefined) return '—'
  if (bps >= 1e9) return `${oneDecimal.format(bps / 1e9)} Gbps`
  if (bps >= 1e6) return `${oneDecimal.format(bps / 1e6)} Mbps`
  if (bps >= 1e3) return `${noDecimals.format(bps / 1e3)} kbps`
  return `${noDecimals.format(bps)} bps`
}

/** Una barra del gráfico de consumo: un día o una hora. */
export interface TrafficBucket {
  start: number
  label: string
  /** Para el tooltip y la tabla: "sáb 31 oct" o "31 oct, 3 p. m.". */
  title: string
  down: number
  up: number
  peakDown: number
  peakUp: number
}

const DAY_LABEL = new Intl.DateTimeFormat('es-CO', { weekday: 'short', day: 'numeric' })
const DAY_TITLE = new Intl.DateTimeFormat('es-CO', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
})
const HOUR_LABEL = new Intl.DateTimeFormat('es-CO', { hour: 'numeric' })
const HOUR_TITLE = new Intl.DateTimeFormat('es-CO', {
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
})

function startOfDay(time: number): number {
  const day = new Date(time)
  day.setHours(0, 0, 0, 0)
  return day.getTime()
}

function emptyBucket(start: number, label: string, title: string): TrafficBucket {
  return { start, label, title, down: 0, up: 0, peakDown: 0, peakUp: 0 }
}

function add(bucket: TrafficBucket, row: OnuTrafficHour): void {
  bucket.down += row.down_bytes
  bucket.up += row.up_bytes
  bucket.peakDown = Math.max(bucket.peakDown, row.peak_down_bps)
  bucket.peakUp = Math.max(bucket.peakUp, row.peak_up_bps)
}

/** Los últimos `days` días (con hoy), en la hora local del navegador; los días sin datos van
 * en cero para que el eje no salte. */
export function byDay(hours: OnuTrafficHour[], days: number, now = Date.now()): TrafficBucket[] {
  const today = startOfDay(now)
  const buckets: TrafficBucket[] = []
  for (let i = days - 1; i >= 0; i -= 1) {
    const day = new Date(today)
    day.setDate(day.getDate() - i)
    buckets.push(emptyBucket(day.getTime(), DAY_LABEL.format(day), DAY_TITLE.format(day)))
  }
  const index = new Map(buckets.map((bucket, i) => [bucket.start, i]))
  for (const row of hours) {
    const i = index.get(startOfDay(Date.parse(row.hour)))
    const bucket = i === undefined ? undefined : buckets[i]
    if (bucket) add(bucket, row)
  }
  return buckets
}

/** Las últimas `count` horas (con la actual). */
export function byHour(hours: OnuTrafficHour[], count: number, now = Date.now()): TrafficBucket[] {
  const current = new Date(now)
  current.setMinutes(0, 0, 0)
  const buckets: TrafficBucket[] = []
  for (let i = count - 1; i >= 0; i -= 1) {
    const hour = new Date(current.getTime() - i * 3600_000)
    buckets.push(emptyBucket(hour.getTime(), HOUR_LABEL.format(hour), HOUR_TITLE.format(hour)))
  }
  const index = new Map(buckets.map((bucket, i) => [bucket.start, i]))
  for (const row of hours) {
    const i = index.get(Date.parse(row.hour))
    const bucket = i === undefined ? undefined : buckets[i]
    if (bucket) add(bucket, row)
  }
  return buckets
}

export interface TrafficTotal {
  down: number
  up: number
}

/** Hoy, los últimos 7 días y lo que va del mes (hora local). */
export function totals(
  hours: OnuTrafficHour[],
  now = Date.now(),
): { today: TrafficTotal; week: TrafficTotal; month: TrafficTotal } {
  const today = startOfDay(now)
  const weekStart = new Date(today)
  weekStart.setDate(weekStart.getDate() - 6)
  const monthStart = new Date(today)
  monthStart.setDate(1)
  const result = {
    today: { down: 0, up: 0 },
    week: { down: 0, up: 0 },
    month: { down: 0, up: 0 },
  }
  for (const row of hours) {
    const time = Date.parse(row.hour)
    const ranges: [TrafficTotal, number][] = [
      [result.today, today],
      [result.week, weekStart.getTime()],
      [result.month, monthStart.getTime()],
    ]
    for (const [total, from] of ranges) {
      if (time >= from && time <= now) {
        total.down += row.down_bytes
        total.up += row.up_bytes
      }
    }
  }
  return result
}

/** Un tope "redondo" para el eje: 1, 2 o 5 por potencia de diez, con al menos `value`. */
export function niceCeiling(value: number): number {
  if (value <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const step = [1, 2, 5, 10].find((factor) => factor * power >= value) ?? 10
  return step * power
}
