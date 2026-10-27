import type { AuditEntry } from '@/api'

/** Lo que hizo cada entrada de la bitácora, en palabras del operador. */
const ACTIONS: Record<string, string> = {
  'auth.login': 'Entró',
  'auth.login_failed': 'Intento de entrada fallido',
  'auth.password_changed': 'Cambió su contraseña',
  'olt.create': 'Agregó una OLT',
  'olt.update': 'Editó una OLT',
  'olt.delete': 'Eliminó una OLT',
  'olt.password_change_start': 'Pidió cambiar la clave de una OLT',
  'olt.password_change': 'Cambió la clave de una OLT',
  'onu.authorize': 'Dio de alta una ONU',
  'onu.configure': 'Configuró internet y WiFi de una ONU',
  'onu.reboot': 'Reinició una ONU',
  'onu.delete': 'Desautorizó una ONU',
  'template.create': 'Creó un plan',
  'template.update': 'Editó un plan',
  'template.delete': 'Eliminó un plan',
  'tunnel.router.create': 'Agregó un MikroTik al túnel',
  'tunnel.router.rotate_keys': 'Rotó las llaves de un MikroTik',
  'reconciliation.run': 'Corrió una conciliación',
  'map.box.create': 'Agregó una caja al mapa',
  'map.box.update': 'Editó una caja del mapa',
  'map.box.delete': 'Eliminó una caja del mapa',
  'map.port.assign': 'Puso un cliente en un puerto',
  'map.port.release': 'Liberó un puerto',
  'map.cable.create': 'Dibujó un cable',
  'map.cable.update': 'Editó un cable',
  'map.cable.delete': 'Eliminó un cable',
  'map.pole.create': 'Agregó un poste',
  'map.pole.delete': 'Eliminó un poste',
  'map.olt.place': 'Ubicó una OLT en el mapa',
}

export function auditAction(action: string): string {
  return ACTIONS[action] ?? action
}

/** Filtros por tipo: prefijo de la acción. */
export const AUDIT_KINDS: { value: string; label: string }[] = [
  { value: '', label: 'Todo' },
  { value: 'olt.', label: 'OLT' },
  { value: 'onu.', label: 'ONU' },
  { value: 'template.', label: 'Planes' },
  { value: 'tunnel.', label: 'Túnel' },
  { value: 'auth.', label: 'Entradas' },
  { value: 'reconciliation.', label: 'Conciliación' },
  { value: 'map.', label: 'Mapa' },
]

const FIELDS: Record<string, string> = {
  name: 'Nombre',
  model: 'Modelo',
  firmware: 'Firmware',
  real_ip: 'IP en la red',
  nat_ip: 'IP del túnel',
  ssh_port: 'Puerto SSH',
  cli_protocol: 'Acceso a la CLI',
  telnet_port: 'Puerto Telnet',
  snmp_port: 'Puerto SNMP',
  credenciales: 'Credenciales',
  used_default_credentials: 'Clave de fábrica',
  username: 'Usuario',
  changed: 'Claves cambiadas',
  saved: 'Guardada en la OLT',
  login: 'Clave de acceso',
  enable: 'Clave de enable',
  template: 'Plan',
  pon: 'PON',
  onu: 'Posición',
  serial: 'Serial',
  description: 'Cliente',
  equipment_id: 'Modelo de ONU',
  pppoe_user: 'PPPoE',
  wifi_ssid: 'WiFi',
  routeros_version: 'RouterOS',
  transport: 'Transporte',
  kind: 'Tipo',
  port: 'Puerto',
  fibers: 'Hilos',
  length_m: 'Metros',
  claves_onu: 'Claves de la ONU',
}

// Identificadores internos y el cuerpo técnico de un plan: no le dicen nada al operador.
const HIDDEN = new Set(['job_id', 'router_id', 'id', 'tenant_id', 'body'])

const VALUES: Record<string, string> = {
  login: 'acceso',
  enable: 'enable',
  password: 'clave',
  enable_password: 'clave de enable',
  snmp_community: 'comunidad SNMP',
  onu_admin_password: 'administración',
  onu_user_password: 'cuenta del cliente',
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Sí' : 'No'
  if (Array.isArray(value)) {
    return value.length ? value.map((v) => VALUES[String(v)] ?? String(v)).join(', ') : '—'
  }
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value) // puertos y posiciones, sin separador de miles
  return JSON.stringify(value)
}

export interface AuditDetail {
  label: string
  value: string
  /** Si cambió: el valor de antes. */
  before?: string
}

/** Los datos de la entrada: con antes y después, solo lo que cambió. */
export function auditDetails(entry: Pick<AuditEntry, 'before' | 'after'>): AuditDetail[] {
  const after = entry.after ?? {}
  const before = entry.before ?? {}
  const keys = [...new Set([...Object.keys(after), ...Object.keys(before)])].filter(
    (key) => !HIDDEN.has(key),
  )
  const details: AuditDetail[] = []
  for (const key of keys) {
    const label = FIELDS[key] ?? key
    const now = show(after[key])
    if (entry.before && entry.after && key in before && key in after) {
      const was = show(before[key])
      if (was !== now) details.push({ label, value: now, before: was })
      continue
    }
    const value = key in after ? now : show(before[key])
    if (value !== '—') details.push({ label, value }) // un dato vacío no dice nada
  }
  return details
}
