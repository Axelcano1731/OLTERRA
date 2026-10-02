import type { PlanStatus, ReconFileKind, ReconSummary } from '@/api'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export const PLAN_STATUS: Record<PlanStatus, { label: string; tone: Tone }> = {
  queued: { label: 'En cola', tone: 'info' },
  ok: { label: 'Completo', tone: 'success' },
  partial: { label: 'Parcial', tone: 'warning' },
  failed: { label: 'Falló', tone: 'danger' },
  expired: { label: 'Venció', tone: 'danger' },
  rejected: { label: 'Rechazado', tone: 'danger' },
}

export function planStatus(status: string): { label: string; tone: Tone } {
  return PLAN_STATUS[status as PlanStatus] ?? { label: status, tone: 'neutral' }
}

export function oltStatus(status: string): { label: string; tone: Tone } {
  // Hoy toda OLT queda 'pending' hasta que exista el descubrimiento (fase 1).
  if (status === 'pending') return { label: 'Sin descubrir', tone: 'neutral' }
  return { label: status, tone: 'neutral' }
}

export const RECON_SOURCE: Record<ReconSummary['source'], string> = {
  api: 'API',
  upload: 'Archivos',
  demo: 'Demo',
}

/** "llave:local" → "Llave local": así guarda la API quién pidió algo. */
export function actorLabel(actor: string): string {
  const separator = actor.indexOf(':')
  const kind = actor.slice(0, separator)
  const name = actor.slice(separator + 1)
  return separator > 0 && kind === 'llave' && name ? `Llave ${name}` : actor
}

export const FILE_KIND: Record<ReconFileKind, string> = {
  onus: 'ONUs',
  secrets: 'Secretos PPPoE',
  sessions: 'Sesiones activas',
  customers: 'Clientes del CRM',
}
