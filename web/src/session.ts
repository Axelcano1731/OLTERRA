// Sesión de la interfaz: el token de quien entró (usuario y contraseña, o una llave de API para
// integraciones) y a quién pertenece.
//
// El token queda en sessionStorage, que se borra al cerrar la pestaña; con "mantener la sesión",
// en localStorage. La CSP del servidor y no usar v-html cierran la puerta a que un script ajeno
// lo lea. Un token de usuario vence solo (12 horas, o 30 días si se pidió mantenerla).

import { computed, reactive, readonly } from 'vue'

import { getMe, login, logout, type Me } from '@/api'

const STORAGE_KEY = 'olterra.llave'

interface SessionState {
  key: string | null
  me: Me | null
}

function storages(): Storage[] {
  try {
    return [window.sessionStorage, window.localStorage]
  } catch {
    return [] // almacenamiento bloqueado: la sesión vive solo en memoria
  }
}

function storedKey(): string | null {
  for (const storage of storages()) {
    try {
      const value = storage.getItem(STORAGE_KEY)
      if (value) return value
    } catch {
      // sigue con el siguiente
    }
  }
  return null
}

function forget(): void {
  for (const storage of storages()) {
    try {
      storage.removeItem(STORAGE_KEY)
    } catch {
      // nada que hacer
    }
  }
}

const state = reactive<SessionState>({ key: storedKey(), me: null })

export const session = readonly(state)

export const isConnected = computed(() => state.key !== null)

/** Entró con la contraseña inicial: hasta cambiarla no puede hacer nada más. */
export const mustChangePassword = computed(() => state.me?.user?.must_change_password === true)

function keep(token: string, me: Me, remember: boolean): void {
  forget()
  const [sessionStore, localStore] = storages()
  try {
    ;(remember ? localStore : sessionStore)?.setItem(STORAGE_KEY, token)
  } catch {
    // sin almacenamiento: dura lo que dure la pestaña
  }
  state.key = token
  state.me = me
}

/** Entra con usuario y contraseña. */
export async function signIn(username: string, password: string, remember: boolean): Promise<Me> {
  const { token } = await login(username.trim(), password, remember)
  const me = await getMe(token)
  keep(token, me, remember)
  return me
}

/** Prueba una llave de API (integraciones) y, si sirve, la deja como sesión. */
export async function connect(key: string, remember: boolean): Promise<void> {
  const clean = key.trim()
  keep(clean, await getMe(clean), remember)
}

/** Vuelve a leer quién es (después de cambiar la contraseña inicial). */
export async function refreshMe(): Promise<void> {
  if (state.key) state.me = await getMe()
}

/** Sale: cierra la sesión en el servidor (si es de usuario) y la olvida aquí. */
export async function signOut(): Promise<void> {
  if (state.key?.startsWith('ols_')) {
    try {
      await logout()
    } catch {
      // vencida o sin conexión: igual se olvida aquí
    }
  }
  disconnect()
}

/** Carga a quién pertenece la llave guardada (al abrir la app). */
export async function loadMe(): Promise<Me | null> {
  if (state.key && !state.me) state.me = await getMe()
  return state.me
}

export function disconnect(): void {
  forget()
  state.key = null
  state.me = null
}

export function can(scope: string): boolean {
  const scopes = state.me?.scopes ?? []
  return scopes.includes('*') || scopes.includes(scope)
}
