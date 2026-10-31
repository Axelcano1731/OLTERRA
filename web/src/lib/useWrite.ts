import { computed, ref, shallowRef } from 'vue'

import type { WritePlan } from '@/api'

import { errorText } from './useAsync'
import { usePlan } from './usePlan'

export interface WriteLabels {
  /** Mientras la OLT lo hace. */
  busy: string
  /** Cuando la OLT lo hizo. */
  ok: string
}

/** Una escritura en la OLT a la vez, con su mensaje de "haciendo", "listo" o qué falló. */
export function useWrite<K extends string>(labels: Record<K, WriteLabels>) {
  const writes = usePlan()
  const kind = shallowRef<K | null>(null)
  const startError = ref<string | null>(null)

  const state = computed<{ tone: 'muted' | 'success' | 'danger'; text: string } | null>(() => {
    const current: K | null = kind.value
    if (current === null) return null
    const label: WriteLabels = labels[current]
    const failure = startError.value ?? writes.error.value
    if (failure) return { tone: 'danger', text: failure }
    const result = writes.plan.value?.result
    if (writes.waiting.value || !result) {
      return {
        tone: 'muted',
        text: label.busy + (writes.slow.value ? ' (la OLT está tardando)' : ''),
      }
    }
    if (result.status === 'ok') return { tone: 'success', text: label.ok }
    const failed = result.outputs?.find((output) => !output.ok)
    return {
      tone: 'danger',
      text: `La OLT no lo hizo: ${failed?.error ?? result.error ?? 'sin respuesta'}`,
    }
  })

  /** Corre la escritura y devuelve si la OLT la hizo. */
  async function run(next: K, start: () => Promise<WritePlan>): Promise<boolean> {
    kind.value = next
    startError.value = null
    try {
      const plan = await start()
      await writes.follow(plan.plan_id)
      return writes.plan.value?.result?.status === 'ok'
    } catch (caught) {
      startError.value = errorText(caught)
      return false
    }
  }

  function reset(): void {
    writes.stop()
    kind.value = null
    startError.value = null
  }

  return { run, reset, state, kind, waiting: writes.waiting }
}
