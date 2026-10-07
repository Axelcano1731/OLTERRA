// Cliente HTTP de la API. La llave viaja solo en Authorization, nunca en la URL.

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface ClientHooks {
  getKey: () => string | null
  onUnauthorized: () => void
}

let hooks: ClientHooks = { getKey: () => null, onUnauthorized: () => {} }

export function configureClient(next: ClientHooks): void {
  hooks = next
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  json?: unknown
  form?: FormData
  signal?: AbortSignal
  /** false para endpoints públicos (/health). */
  auth?: boolean
  /** Llave a probar (pantalla de conexión): un 401 no cierra la sesión actual. */
  key?: string
}

const STATUS_MESSAGES: Record<number, string> = {
  401: 'La llave de API no es válida o fue revocada.',
  403: 'Tu llave no tiene permiso para esto.',
  404: 'No existe o no es de tu ISP.',
  413: 'Los archivos pasan del tamaño permitido.',
  502: 'La API no responde en este momento.',
  503: 'La API no está disponible en este momento.',
  504: 'La API tardó demasiado en responder.',
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

function describe(issue: ValidationIssue): string {
  const where = (issue.loc ?? []).filter((part) => part !== 'body').join('.')
  return where ? `${where}: ${issue.msg ?? 'inválido'}` : (issue.msg ?? 'inválido')
}

/** FastAPI responde {detail: "texto"} o, si falla la validación, {detail: [{loc, msg}]}. */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail))
      return (body.detail as ValidationIssue[]).map(describe).join(' · ')
  } catch {
    // Sin cuerpo JSON (un proxy, un 502): queda el mensaje por código.
  }
  return STATUS_MESSAGES[response.status] ?? `Error ${response.status} de la API.`
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const key = options.key ?? (options.auth === false ? null : hooks.getKey())
  if (key) headers.Authorization = `Bearer ${key}`

  let body: BodyInit | undefined
  if (options.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.json)
  } else if (options.form) {
    body = options.form // el navegador pone el boundary del multipart
  }

  let response: Response
  try {
    response = await fetch(path, {
      method: options.method ?? (body ? 'POST' : 'GET'),
      headers,
      body,
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'No hay conexión con la API de Olterra.')
  }

  if (!response.ok) {
    const message = await errorMessage(response)
    if (response.status === 401 && options.key === undefined && options.auth !== false) {
      hooks.onUnauthorized()
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return undefined as T // sin cuerpo (borrados)
  return (await response.json()) as T
}
