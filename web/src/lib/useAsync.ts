import { onMounted, ref, shallowRef, type Ref } from 'vue'

import { ApiError } from '@/api'

export interface AsyncState<T> {
  data: Ref<T | undefined>
  error: Ref<string | null>
  loading: Ref<boolean>
  reload: () => Promise<void>
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : 'Algo salió mal.'
}

/** Carga datos al montar el componente y deja el error listo para mostrar. */
export function useAsync<T>(loader: () => Promise<T>, immediate = true): AsyncState<T> {
  const data = shallowRef<T>()
  const error = ref<string | null>(null)
  const loading = ref(false)

  async function reload(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      data.value = await loader()
    } catch (caught) {
      error.value = errorText(caught)
    } finally {
      loading.value = false
    }
  }

  if (immediate) onMounted(reload)
  return { data, error, loading, reload }
}
