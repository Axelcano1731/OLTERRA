import type { PlanStatus, ProvisionJob, ReconFileKind, ReconSummary } from '@/api'

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
  // Lo actualiza cada consulta o escritura: respondió (online) o no se pudo entrar.
  if (status === 'pending') return { label: 'Sin consultar', tone: 'neutral' }
  if (status === 'online') return { label: 'Responde', tone: 'success' }
  if (status === 'unreachable') return { label: 'Sin respuesta', tone: 'danger' }
  return { label: status, tone: 'neutral' }
}

export const JOB_STATUS: Record<ProvisionJob['status'], { label: string; tone: Tone }> = {
  running: { label: 'En curso', tone: 'accent' },
  done: { label: 'Listo', tone: 'success' },
  failed: { label: 'No terminó', tone: 'danger' },
}

export const RECON_SOURCE: Record<ReconSummary['source'], string> = {
  api: 'API',
  upload: 'Archivos',
  demo: 'Demo',
}

/** "llave:local" → "Llave local", "usuario:ana" → "ana": así guarda la API quién pidió algo. */
export function actorLabel(actor: string): string {
  const separator = actor.indexOf(':')
  const kind = actor.slice(0, separator)
  const name = actor.slice(separator + 1)
  if (separator <= 0 || !name) return actor
  if (kind === 'llave') return `Llave ${name}`
  if (kind === 'usuario') return name
  if (kind === 'olterra') return 'Olterra (automático)'
  return actor
}

export const FILE_KIND: Record<ReconFileKind, string> = {
  onus: 'ONUs',
  secrets: 'Secretos PPPoE',
  sessions: 'Sesiones activas',
  customers: 'Clientes del CRM',
}
