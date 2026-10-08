// Reflejo de src/olterra/api/schemas.py. Si cambia un esquema, cambia aquí también.

export interface Health {
  status: 'ok' | 'degradado'
  version: string
  database: 'ok' | 'error'
  nats: 'conectado' | 'sin conexión'
  vault: 'lista' | 'sin llave maestra'
}

export interface UserInfo {
  username: string
  display_name: string
  role: 'admin' | 'tecnico' | 'lectura'
  must_change_password: boolean
}

export interface Me {
  tenant: { id: string; slug: string; name: string }
  key_name: string
  scopes: string[]
  /** Solo si entró con usuario y contraseña (no con una llave de API). */
  user: UserInfo | null
}

export interface LoginOut {
  /** Va en Authorization: Bearer, igual que una llave de API. */
  token: string
  expires_at: string
  must_change_password: boolean
}

export interface Olt {
  id: string
  name: string
  driver: string
  model: string | null
  firmware: string | null
  router_id: string | null
  real_ip: string | null
  nat_ip: string | null
  ssh_port: number
  snmp_port: number
  /** pending (nunca consultada), online o unreachable: lo actualiza cada consulta. */
  status: string
  last_seen_at: string | null
  created_at: string
}

export interface OltCreate {
  name: string
  model?: string
  firmware?: string
  router_id?: string
  real_ip?: string
  ssh_port: number
  snmp_port: number
  /** Vacío: el usuario de fábrica de VSOL (lo informa /v1/olts/defaults). */
  username?: string
  /** Vacío en una OLT nueva: el servidor usa su clave de fábrica. */
  password?: string
  enable_password?: string
  snmp_community?: string
  latitude?: number
  longitude?: number
}

export interface OltCreated extends Olt {
  used_default_credentials: boolean
}

export interface OltDefaults {
  username: string
  password_configured: boolean
}

/** Solo lo que cambia. En enable y comunidad SNMP, una cadena vacía la quita. */
export interface OltUpdate {
  model?: string
  firmware?: string
  real_ip?: string
  ssh_port?: number
  snmp_port?: number
  username?: string
  password?: string
  enable_password?: string
  snmp_community?: string
}

export type CommandScope = 'olt' | 'pon' | 'onu'

export interface Command {
  key: string
  command: string
  scope: CommandScope
  verified: boolean
  parsed: boolean
  notes: string
}

export interface QueryRequest {
  commands: string[]
  pon: number[]
  onu: string[]
}

export type PlanStatus = 'queued' | 'ok' | 'partial' | 'failed' | 'expired' | 'rejected'

export interface PlanOutput {
  key: string
  params: Record<string, number | string>
  ok: boolean
  error: string | null
  output: string | null
  data?: unknown
  parse_error?: string
}

/** Un paso de cambio de modo (configure terminal…) que falló: no es una llamada del catálogo. */
export interface SessionError {
  command: string
  error: string | null
  output: string | null
}

export interface PlanResult {
  status: PlanStatus
  error: string | null
  executor: string
  host_key: string | null
  outputs?: PlanOutput[]
  session_errors?: SessionError[]
}

export interface Plan {
  plan_id: string
  olt_id: string
  status: PlanStatus
  created_at: string
  finished_at: string | null
  result: PlanResult | null
}

export interface PlanSummary {
  plan_id: string
  status: PlanStatus
  requested_by: string
  commands: string[]
  created_at: string
  finished_at: string | null
}

export interface TunnelRouter {
  id: string
  name: string
  peer_index: number
  overlay_ip: string
  /** wireguard (RouterOS 7) o sstp (RouterOS 6) */
  transport: 'wireguard' | 'sstp'
  wg_public_key: string | null
  ppp_user: string | null
  routeros_version: string | null
  created_at: string
}

export interface RouterScripts {
  router: TunnelRouter
  isp_script: string
  hub_script: string
}

export type Severity = 'error' | 'advertencia' | 'info'

export interface Finding {
  kind: string
  severity: Severity
  title: string
  detail: string
  suggestion: string
  refs: Record<string, string | number | null>
}

export type ReconFileKind = 'onus' | 'secrets' | 'sessions' | 'customers'

export interface ReconFile {
  kind: ReconFileKind
  name: string
  records: number
}

export interface ReconSummary {
  id: string
  created_at: string
  requested_by: string
  source: 'api' | 'upload' | 'demo'
  files: ReconFile[]
  counts: Record<string, number>
}

export interface Recon extends ReconSummary {
  findings: Finding[]
}

// --- Aprovisionamiento (api/schemas.py y drivers/vsol_gpon/provisioning.py) ---------------

export interface Tcont {
  id: number
  name: string
  dba: string
}

export interface Gemport {
  id: number
  tcont: number
  name: string
  limit_down: string | null
}

export interface OnuService {
  name: string
  gemport: number
  vlan: number
}

export interface ServicePort {
  id: number
  gemport: number
  user_vlan: number
  vlan: number
  cos: number
}

/** WAN en modo router con PPPoE: la ONU marca. */
export interface Wan {
  index: number
  mtu: number
  vlan: number
  cos: number
  nat: boolean
  binds: string[]
}

export type WanService = 'telnet' | 'ftp' | 'http' | 'https' | 'tftp' | 'ssh'

/** Gestión remota de la ONU: firewall y qué responde desde internet (la WAN). */
export interface Management {
  /** null = no se toca. */
  firewall: 'disable' | 'low' | 'middle' | 'high' | null
  ping_wan: boolean
  /** Lo que no está aquí se cierra desde la WAN. Desde la LAN todo sigue abierto. */
  wan_access: WanService[]
}

export interface TemplateBody {
  auth_profile: string
  onu_profile: string | null
  tconts: Tcont[]
  gemports: Gemport[]
  services: OnuService[]
  service_ports: ServicePort[]
  wan: Wan | null
  wifi: { ssid_index: number } | null
  management: Management | null
}

export interface ProvisionTemplate {
  id: string
  name: string
  driver: string
  body: TemplateBody
  created_at: string
  updated_at: string
}

export interface TemplateIn {
  name: string
  body: TemplateBody
}

/** PPPoE y WiFi del cliente. Las claves solo viajan selladas; la API no las devuelve nunca. */
interface CustomerService {
  template_id: string
  pppoe_user?: string
  pppoe_password?: string
  /** Como lo escribe el cliente: tildes y espacios los arregla Olterra (la OLT no los acepta). */
  wifi_name?: string
  wifi_key?: string
}

/** Alta de una ONU nueva: lo que sabe quien aprovisiona. Lo técnico lo resuelve Olterra. */
export interface AuthorizeIn extends CustomerService {
  pon: number
  serial: string
  /** Nombre del cliente tal cual (José Pérez → Jose_Perez en la OLT). */
  customer: string
  equipment_id?: string
  /** Vacío: la primera posición libre del PON. */
  onu?: number
}

/** Internet y WiFi de una ONU ya autorizada. */
export interface ConfigureIn extends CustomerService {
  pon: number
  onu: number
}

export type JobStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface JobStep {
  key: string
  label: string
  status: JobStepStatus
  message: string | null
}

/** Un alta (o "configurar internet") que avanza sola en el servidor. */
export interface ProvisionJob {
  id: string
  olt_id: string
  kind: 'authorize' | 'configure'
  status: 'running' | 'done' | 'failed'
  step: string
  template_name: string | null
  pon: number | null
  onu: number | null
  serial: string | null
  description: string | null
  pppoe_user: string | null
  wifi_ssid: string | null
  equipment_id: string | null
  phase: string | null
  rx_dbm: number | null
  /** Comandos sin validar que corre (solo en modo laboratorio). */
  unverified: string[]
  steps: JobStep[]
  error: string | null
  created_at: string
  finished_at: string | null
}

/** Una ONU de la OLT según "show interface brief": dónde está, de quién es y si está arriba. */
export interface OnuPort {
  pon: number
  onu: number
  description: string | null
  up: boolean
}

export interface InterfacesBrief {
  pons: number[]
  onus: OnuPort[]
}

/** Una ONU conectada que la OLT todavía no autoriza. */
export interface AutofindRow {
  pon: number | null
  serial: string
  index: number | null
  model: string | null
}

export interface OnuRef {
  pon: number
  onu: number
}

export interface WritePlan {
  plan_id: string
  olt_id: string
  status: PlanStatus
  created_at: string
  /** Comandos sin captura de laboratorio que este plan corre (solo en modo laboratorio). */
  unverified: string[]
}

/** Lo que devuelve la consulta onu.service_config ya interpretada ("copiar una ONU"). */
export interface OnuRunningConfig {
  onu: number
  serial: string | null
  template: TemplateBody
  client: {
    serial?: string
    description?: string
    equipment_id?: string
    pppoe_user?: string
    wifi_ssid?: string
  }
  ignored: string[]
}
