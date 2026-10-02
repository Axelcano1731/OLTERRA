// Reflejo de src/olterra/api/schemas.py. Si cambia un esquema, cambia aquí también.

export interface Health {
  status: 'ok' | 'degradado'
  version: string
  database: 'ok' | 'error'
  nats: 'conectado' | 'sin conexión'
  vault: 'lista' | 'sin llave maestra'
}

export interface Me {
  tenant: { id: string; slug: string; name: string }
  key_name: string
  scopes: string[]
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
  status: string
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
  username: string
  password: string
  enable_password?: string
  snmp_community?: string
  latitude?: number
  longitude?: number
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

export interface PlanResult {
  status: PlanStatus
  error: string | null
  executor: string
  host_key: string | null
  outputs?: PlanOutput[]
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
