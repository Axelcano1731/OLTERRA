import { onBeforeUnmount, ref, shallowRef } from 'vue'

import { getJob, type ProvisionJob } from '@/api'

import { errorText } from './useAsync'

/** Sigue un trabajo de alta hasta que termina (lo avanza el servidor; aquí solo se mira). */
export function useJob() {
  const job = shallowRef<ProvisionJob | null>(null)
  const error = ref<string | null>(null)
  let timer: number | undefined
  let generation = 0

  function stop(): void {
    generation += 1
    window.clearTimeout(timer)
  }

  function follow(first: ProvisionJob, onFinish?: (job: ProvisionJob) => void): void {
    stop()
    const mine = generation
    job.value = first
    error.value = null
    const tick = async (): Promise<void> => {
      if (mine !== generation) return
      try {
        const current = await getJob(first.id)
        if (mine !== generation) return
        job.value = current
        error.value = null
        if (current.status !== 'running') {
          onFinish?.(current)
          return
        }
      } catch (caught) {
        if (mine !== generation) return
        error.value = errorText(caught) // se reintenta: puede ser un corte de red
      }
      timer = window.setTimeout(() => void tick(), 2000)
    }
    if (first.status === 'running') timer = window.setTimeout(() => void tick(), 1500)
    else onFinish?.(first)
  }

  onBeforeUnmount(stop)
  return { job, error, follow, stop }
}
