// Sesión de la interfaz: la llave de API del ISP y a quién pertenece.
//
// Fase 0: no hay usuarios todavía (falta decidir el proveedor de identidad, ver
// ARQUITECTURA A.10). La llave queda en sessionStorage, que se borra al cerrar la
// pestaña; con "recordar", en localStorage. La CSP del servidor y no usar v-html
// cierran la puerta a que un script ajeno la lea.

import { computed, reactive, readonly } from 'vue'

import { getMe, type Me } from '@/api'

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

/** Prueba la llave contra la API y, si sirve, la deja como sesión. */
export async function connect(key: string, remember: boolean): Promise<void> {
  const clean = key.trim()
  const me = await getMe(clean)
  forget()
  const [sessionStore, localStore] = storages()
  try {
    ;(remember ? localStore : sessionStore)?.setItem(STORAGE_KEY, clean)
  } catch {
    // sin almacenamiento: dura lo que dure la pestaña
  }
  state.key = clean
  state.me = me
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
