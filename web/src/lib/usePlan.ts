import { onBeforeUnmount, ref, shallowRef } from 'vue'

import { getPlan, type Plan } from '@/api'

import { errorText } from './useAsync'

const SLOW_AFTER_MS = 20_000
const GIVE_UP_AFTER_MS = 5 * 60_000

/** Sigue un plan hasta que el ejecutor devuelve su resultado (o se vence la espera). */
export function usePlan() {
  const plan = shallowRef<Plan | null>(null)
  const error = ref<string | null>(null)
  const waiting = ref(false)
  const slow = ref(false)
  let timer: number | undefined
  let controller: AbortController | undefined
  let generation = 0

  function stop(): void {
    generation += 1
    window.clearTimeout(timer)
    controller?.abort()
    waiting.value = false
  }

  async function follow(planId: string): Promise<void> {
    stop()
    const mine = generation
    const startedAt = Date.now()
    plan.value = null
    error.value = null
    slow.value = false
    waiting.value = true

    const tick = async (): Promise<void> => {
      controller = new AbortController()
      try {
        const current = await getPlan(planId, controller.signal)
        if (mine !== generation) return
        plan.value = current
        if (current.status !== 'queued') {
          waiting.value = false
          return
        }
      } catch (caught) {
        if (mine !== generation) return
        error.value = errorText(caught)
        waiting.value = false
        return
      }
      const elapsed = Date.now() - startedAt
      slow.value = elapsed > SLOW_AFTER_MS
      if (elapsed > GIVE_UP_AFTER_MS) {
        waiting.value = false
        error.value =
          'El plan sigue en cola después de 5 minutos. Revisa que el ejecutor esté corriendo y conectado a NATS.'
        return
      }
      timer = window.setTimeout(() => void tick(), elapsed < 10_000 ? 1000 : 3000)
    }
    await tick()
  }

  onBeforeUnmount(stop)
  return { plan, error, waiting, slow, follow, stop }
}
