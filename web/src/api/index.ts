import { api } from './client'
import type {
  Command,
  Health,
  Me,
  Olt,
  OltCreate,
  OltCreated,
  OltDefaults,
  OltUpdate,
  OnuRef,
  ProvisionTemplate,
  TemplateIn,
  AuthorizeRequest,
  ConfigureRequest,
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

export const authorizeOnu = (oltId: string, body: AuthorizeRequest) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/authorize`, { json: body })
export const configureOnu = (oltId: string, body: ConfigureRequest) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/configure`, { json: body })
export const rebootOnu =(oltId: string, body: OnuRef) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/reboot`, { json: body })
export const deleteOnu = (oltId: string, body: OnuRef) =>
  api<WritePlan>(`/v1/olts/${encodeURIComponent(oltId)}/onus/delete`, { json: body })

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
