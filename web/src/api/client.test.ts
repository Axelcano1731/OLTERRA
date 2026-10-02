import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, configureClient } from './client'

function respond(status: number, body?: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(body === undefined ? null : JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  configureClient({ getKey: () => null, onUnauthorized: () => {} })
})

describe('api', () => {
  it('manda la llave en Authorization, nunca en la URL', async () => {
    configureClient({ getKey: () => 'olt_llave', onUnauthorized: () => {} })
    respond(200, [])
    await api('/v1/olts')
    const [url, init] = vi.mocked(fetch).mock.calls[0]!
    expect(url).toBe('/v1/olts')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer olt_llave')
  })

  it('usa el detail de FastAPI como mensaje', async () => {
    respond(409, { detail: 'Ya existe una OLT con ese nombre o esa IP' })
    await expect(api('/v1/olts', { json: {} })).rejects.toMatchObject({
      status: 409,
      message: 'Ya existe una OLT con ese nombre o esa IP',
    })
  })

  it('arma un mensaje legible con los errores de validación', async () => {
    respond(422, { detail: [{ loc: ['body', 'ssh_port'], msg: 'debe ser ≤ 65535' }] })
    await expect(api('/v1/olts', { json: {} })).rejects.toThrow('ssh_port: debe ser ≤ 65535')
  })

  it('un 401 cierra la sesión, salvo cuando se prueba una llave nueva', async () => {
    const onUnauthorized = vi.fn()
    configureClient({ getKey: () => 'olt_vieja', onUnauthorized })
    respond(401, { detail: 'Llave de API inválida o revocada' })
    await expect(api('/v1/me', { key: 'olt_nueva' })).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).not.toHaveBeenCalled()
    await expect(api('/v1/olts')).rejects.toBeInstanceOf(ApiError)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('sin red da un error claro', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(api('/health', { auth: false })).rejects.toMatchObject({ status: 0 })
  })
})
