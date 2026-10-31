import { computed, ref } from 'vue'

import { queryOlt, type PlanOutput } from '@/api'

import { errorText } from './useAsync'
import { usePlan } from './usePlan'

/** Lectura de solo lectura a la OLT sobre una ONU (sus comandos por ONU o por su PON). */
export function useOnuRead() {
  const plan = usePlan()
  const startError = ref<string | null>(null)
  const asked = ref(false)

  async function run(oltId: string, pon: number, onu: number, commands: string[]): Promise<void> {
    startError.value = null
    asked.value = true
    try {
      const started = await queryOlt(oltId, { commands, pon: [pon], onu: [`${pon}:${onu}`] })
      await plan.follow(started.plan_id)
    } catch (caught) {
      startError.value = errorText(caught)
    }
  }

  /** La salida de un comando, ya interpretada si tiene parser. */
  function output(key: string): PlanOutput | undefined {
    return plan.plan.value?.result?.outputs?.find((item) => item.key === key)
  }

  /** Los datos de un comando, o el motivo por el que no hay. */
  function read<T>(key: string): { data: T | null; error: string | null } {
    const found = output(key)
    if (!found) {
      const finished = !!plan.plan.value?.result && !plan.waiting.value
      const failed = plan.plan.value?.result?.error ?? 'La OLT no respondió'
      return { data: null, error: finished ? failed : null }
    }
    if (!found.ok) return { data: null, error: found.error ?? 'La OLT no respondió' }
    if (found.parse_error) return { data: null, error: found.parse_error }
    return { data: (found.data as T | undefined) ?? null, error: null }
  }

  const error = computed(() => startError.value ?? plan.error.value)

  return {
    run,
    output,
    read,
    asked,
    error,
    waiting: plan.waiting,
    slow: plan.slow,
    done: computed(() => !!plan.plan.value?.result && !plan.waiting.value),
    stop: plan.stop,
  }
}
