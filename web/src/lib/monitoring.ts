import type { Alarm, AlarmKind } from '@/api'

import type { Tone } from './labels'

// Los mismos umbrales que la API (api/monitoring.py): ventana GPON clase B+.
export const RX_LOW_WARNING = -25
export const RX_LOW_CRITICAL = -27
export const RX_HIGH = -8

const dbm = new Intl.NumberFormat('es-CO', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function formatDbm(value: number | null): string {
  return value === null ? '—' : `${dbm.format(value)} dBm`
}

/** La señal en palabras, con su tono de estado (nunca solo color). */
export function signalLevel(rx: number | null): { label: string; tone: Tone } {
  if (rx === null) return { label: 'Sin lectura', tone: 'neutral' }
  if (rx < RX_LOW_CRITICAL) return { label: 'Muy baja', tone: 'danger' }
  if (rx < RX_LOW_WARNING) return { label: 'Baja', tone: 'warning' }
  if (rx > RX_HIGH) return { label: 'Muy alta', tone: 'warning' }
  return { label: 'Buena', tone: 'success' }
}

export const ALARM_KIND: Record<AlarmKind, string> = {
  olt_unreachable: 'OLT sin respuesta',
  pon_down: 'Caída de PON',
  onu_offline: 'ONU caída',
  rx_low: 'Señal baja',
  rx_high: 'Señal alta',
}

export function alarmWhere(alarm: Pick<Alarm, 'olt_name' | 'pon' | 'onu'>): string {
  const olt = alarm.olt_name ?? 'OLT'
  if (alarm.pon === 0) return olt
  if (alarm.onu === 0) return `${olt} · PON ${alarm.pon}`
  return `${olt} · PON ${alarm.pon} · ONU ${alarm.onu}`
}

/** "3 h 20 min", "2 d 4 h": cuánto lleva algo. */
export function duration(fromIso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - new Date(fromIso).getTime()) / 60_000))
  if (minutes < 1) return 'menos de 1 min'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} h ${minutes % 60} min`
  return `${Math.floor(hours / 24)} d ${hours % 24} h`
}
