import { api } from './client'
import type {
  AuditEntry,
  AuditQuery,
  Backup,
  BackupDetail,
  BackupDiff,
  Command,
  Health,
  Me,
  Olt,
  OltCreate,
  OltCreated,
  OltDefaults,
  OltPasswordChange,
  OltUpdate,
  OnuRef,
  ProvisionTemplate,
  TemplateIn,
  AuthorizeIn,
  ConfigureIn,
  LoginOut,
  ProvisionJob,
  WritePlan,
  Plan,
  PlanSummary,
  QueryRequest,
  Recon,
  ReconSummary,
  RouterScripts,
  TunnelRouter,
} from './types'

export { ApiError } from './client'
export type * from './types'

export const getHealth = () => api<Health>('/health', { auth: false })
export const getMe = (key?: string) => api<Me>('/v1/me', { key })
export const login = (username: string, password: string, remember: boolean) =>
  api<LoginOut>('/v1/auth/login', { json: { username, password, remember }, auth: false })
export const logout = () => api<void>('/v1/auth/logout', { method: 'POST' })
export const changePassword = (current: string, next: string) =>
  api<void>('/v1/auth/password', {
    json: { current_password: current, new_password: next },
  })

export const listOlts = () => api<Olt[]>('/v1/olts')
export const getOlt = (id: string) => api<Olt>(`/v1/olts/${encodeURIComponent(id)}`)
export const getOltDefaults = () => api<OltDefaults>('/v1/olts/defaults')
export const createOlt = (body: OltCreate) => api<OltCreated>('/v1/olts', { json: body })
export const deleteOlt = (id: string) =>
  api<void>(`/v1/olts/${encodeURIComponent(id)}`, { method: 'DELETE' })
export const updateOlt = (id: string, body: OltUpdate) =>
  api<Olt>(`/v1/olts/${encodeURIComponent(id)}`, { method: 'PATCH', json: body })
export const listCommands = (id: string) =>
  api<Command[]>(`/v1/olts/${encodeURIComponent(id)}/commands`)
export const queryOlt = (id: string, body: QueryRequest) =>
  api<Plan>(`/v1/olts/${encodeURIComponent(id)}/queries`, { json: body })
export const listPlans = (id: string) =>
  api<PlanSummary[]>(`/v1/olts/${encodeURIComponent(id)}/plans`)
export const getPlan = (id: string, signal?: AbortSignal) =>
  api<Plan>(`/v1/plans/${encodeURIComponent(id)}`, { signal })

export const listTemplates = () => api<ProvisionTemplate[]>('/v1/provision-templates')
export const getTemplate = (id: string) =>
  api<ProvisionTemplate>(`/v1/provision-templates/${encodeURIComponent(id)}`)
export const createTemplate = (body: TemplateIn) =>
  api<ProvisionTemplate>('/v1/provision-templates', { json: body })
export const updateTemplate = (id: string, body: TemplateIn) =>
  api<ProvisionTemplate>(`/v1/provision-templates/${encodeURIComponent(id)}`, {
    method: 'PUT',
    json: body,
  })
export const deleteTemplate = (id: string) =>
  api<void>(`/v1/provision-templates/${encodeURIComponent(id)}`, { method: 'DELETE' })

/** ONU nuevas en todos los PON y la lista de ONU con su cliente, en una sola lectura. */
export const scanOnus = (oltId: string) =>
  api<Plan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/scan`, { method: 'POST' })
export const authorizeOnu = (oltId: string, body: AuthorizeIn) =>
  api<ProvisionJob>(`/v1/olts/${encodeURIComponent(oltId)}/onus/authorize`, { json: body })
export const configureOnu = (oltId: string, body: ConfigureIn) =>
  api<ProvisionJob>(`/v1/olts/${encodeURIComponent(oltId)}/onus/configure`, { json: body })
export const getJob = (id: string, signal?: AbortSignal) =>
  api<ProvisionJob>(`/v1/provision-jobs/${encodeURIComponent(id)}`, { signal })
export const listJobs = (oltId: string, kind: 'onu' | 'olt_password' = 'onu') =>
  api<ProvisionJob[]>(`/v1/olts/${encodeURIComponent(oltId)}/provision-jobs?kind=${kind}`)
/** Cambia la clave en la OLT: la pone, entra con ella y solo entonces la guarda (trabajo). */
export const changeOltPassword = (oltId: string, body: OltPasswordChange) =>
  api<ProvisionJob>(`/v1/olts/${encodeURIComponent(oltId)}/password`, { json: body })
export const rebootOnu = (oltId: string, body: OnuRef) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/reboot`, { json: body })
export const deleteOnu = (oltId: string, body: OnuRef) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/delete`, { json: body })

export const listBackups = (oltId: string) =>
  api<Backup[]>(`/v1/olts/${encodeURIComponent(oltId)}/backups`)
/** Respalda ya la configuración de la OLT (una lectura de show running-config). */
export const backupNow = (oltId: string) =>
  api<Backup>(`/v1/olts/${encodeURIComponent(oltId)}/backups`, { method: 'POST' })
export const getBackup = (id: string) => api<BackupDetail>(`/v1/backups/${encodeURIComponent(id)}`)
/** Qué cambió desde el respaldo anterior (o desde ``against``). */
export const diffBackup = (id: string, against?: string) =>
  api<BackupDiff>(
    `/v1/backups/${encodeURIComponent(id)}/diff` +
      (against ? `?against=${encodeURIComponent(against)}` : ''),
  )

export const listAudit = (query: AuditQuery = {}) => {
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(name, String(value))
  }
  const search = params.toString()
  return api<AuditEntry[]>(`/v1/audit${search ? `?${search}` : ''}`)
}

export const listRouters = () => api<TunnelRouter[]>('/v1/tunnel/routers')
export const createRouter = (name: string, routerosVersion?: string) =>
  api<RouterScripts>('/v1/tunnel/routers', {
    json: { name, routeros_version: routerosVersion || null },
  })
/** Rota la credencial; con otra versión de RouterOS cambia el transporte (6 → SSTP). */
export const rotateRouter = (id: string, routerosVersion?: string) =>
  api<RouterScripts>(`/v1/tunnel/routers/${encodeURIComponent(id)}/script`, {
    method: 'POST',
    json: routerosVersion ? { routeros_version: routerosVersion } : undefined,
  })

export const listRecons = (limit = 20) => api<ReconSummary[]>(`/v1/reconciliations?limit=${limit}`)
export const getRecon = (id: string) => api<Recon>(`/v1/reconciliations/${encodeURIComponent(id)}`)
export const runReconDemo = () => api<Recon>('/v1/reconciliations/demo', { method: 'POST' })
export const runReconFiles = (form: FormData) => api<Recon>('/v1/reconciliations/files', { form })
