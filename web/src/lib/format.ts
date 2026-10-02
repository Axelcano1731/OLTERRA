const dateTime = new Intl.DateTimeFormat('es-CO', { dateStyle: 'medium', timeStyle: 'short' })
const relative = new Intl.RelativeTimeFormat('es', { numeric: 'auto' })

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
]

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso))
}

/** "hace 5 minutos", "ayer"... Menos de un minuto es "hace un momento". */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000)
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.trunc(seconds / size), unit)
  }
  return 'hace un momento'
}

export function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString('es-CO')} ${count === 1 ? one : many}`
}

/** Minúsculas y sin tildes, para buscar "maría" escribiendo "maria". */
export function fold(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
}
