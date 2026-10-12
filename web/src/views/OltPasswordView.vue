<script setup lang="ts">
import { KeyRound, LoaderCircle } from '@lucide/vue'
import { computed, reactive, ref } from 'vue'

import { changeOltPassword, getOlt, listJobs, type OltPasswordChange } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import JobProgress from '@/components/JobProgress.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime } from '@/lib/format'
import { JOB_STATUS } from '@/lib/labels'
import { errorText, useAsync } from '@/lib/useAsync'
import { useJob } from '@/lib/useJob'

const props = defineProps<{ id: string }>()
const olt = useAsync(() => getOlt(props.id))
const history = useAsync(() => listJobs(props.id, 'olt_password'))
const { job, error: followError, follow } = useJob()

const form = reactive({
  password: '',
  passwordAgain: '',
  enable: '',
  enableAgain: '',
})
const submitted = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

// Lo mismo que acepta la API: 8 a 64 caracteres imprimibles, sin espacios ni tildes.
const VALID = /^[!-~]{8,64}$/

function check(value: string, again: string): string | undefined {
  if (!value) return undefined
  if (!VALID.test(value)) return 'De 8 a 64 caracteres, sin espacios ni tildes.'
  if (value !== again) return 'Las dos no coinciden.'
  return undefined
}

const problems = computed(() => ({
  password: check(form.password, form.passwordAgain),
  enable: check(form.enable, form.enableAgain),
  empty:
    !form.password && !form.enable
      ? 'Escribe la clave nueva de acceso, la de enable o las dos.'
      : undefined,
}))

const running = computed(() => job.value?.status === 'running')

async function submit(): Promise<void> {
  submitted.value = true
  error.value = null
  const found = problems.value
  if (found.password || found.enable || found.empty) return
  const body: OltPasswordChange = {}
  if (form.password) body.new_password = form.password
  if (form.enable) body.new_enable_password = form.enable
  busy.value = true
  try {
    const started = await changeOltPassword(props.id, body)
    // Las claves no se quedan en la pantalla: ya van cifradas en el trabajo.
    Object.assign(form, { password: '', passwordAgain: '', enable: '', enableAgain: '' })
    submitted.value = false
    follow(started, () => void history.reload())
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <LoadingBlock v-if="olt.loading.value && !olt.data.value" />
  <AlertBox v-else-if="olt.error.value" tone="danger">{{ olt.error.value }}</AlertBox>

  <template v-else-if="olt.data.value">
    <PageHeader
      title="Cambiar la clave en la OLT"
      description="Olterra pone la clave nueva en la OLT, entra con ella para comprobarla y solo entonces la guarda en la OLT y aquí. Si algo falla, no guarda nada."
      :back="{ name: 'olt', params: { id } }"
      :back-label="olt.data.value.name"
    />

    <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
      <form class="space-y-6" novalidate @submit.prevent="submit">
        <section class="card p-5 sm:p-6">
          <div class="flex items-center gap-2">
            <KeyRound class="size-4 text-muted" />
            <h2 class="font-semibold">Claves nuevas</h2>
          </div>
          <p class="mt-1 text-sm text-muted">
            Es la clave del usuario con el que entra Olterra. Deja vacía la que no quieras cambiar.
          </p>

          <div class="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <label for="password" class="label">Clave de acceso nueva</label>
              <input
                id="password"
                v-model="form.password"
                type="password"
                class="input"
                autocomplete="new-password"
                :disabled="running"
                :aria-invalid="submitted && !!problems.password"
              />
              <p class="hint">La de SSH y la web de la OLT.</p>
            </div>
            <div>
              <label for="password-again" class="label">Repítela</label>
              <input
                id="password-again"
                v-model="form.passwordAgain"
                type="password"
                class="input"
                autocomplete="new-password"
                :disabled="running"
                :aria-invalid="submitted && !!problems.password"
              />
              <p v-if="submitted && problems.password" class="hint text-danger">
                {{ problems.password }}
              </p>
            </div>
            <div>
              <label for="enable" class="label">Clave de enable nueva</label>
              <input
                id="enable"
                v-model="form.enable"
                type="password"
                class="input"
                autocomplete="new-password"
                :disabled="running"
                :aria-invalid="submitted && !!problems.enable"
              />
              <p class="hint">La que pide la OLT al escribir enable.</p>
            </div>
            <div>
              <label for="enable-again" class="label">Repítela</label>
              <input
                id="enable-again"
                v-model="form.enableAgain"
                type="password"
                class="input"
                autocomplete="new-password"
                :disabled="running"
                :aria-invalid="submitted && !!problems.enable"
              />
              <p v-if="submitted && problems.enable" class="hint text-danger">
                {{ problems.enable }}
              </p>
            </div>
          </div>
        </section>

        <AlertBox tone="warning" title="Antes de cambiarla">
          Si otras personas entran a la OLT con ese mismo usuario (por SSH o por la web), van a
          necesitar la clave nueva.
        </AlertBox>

        <AlertBox v-if="submitted && problems.empty" tone="danger">{{ problems.empty }}</AlertBox>
        <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>

        <div class="flex justify-end gap-2">
          <RouterLink :to="{ name: 'olt', params: { id } }" class="btn-secondary"
            >Volver</RouterLink
          >
          <button type="submit" class="btn-primary" :disabled="busy || running">
            <LoaderCircle v-if="busy || running" class="size-4 animate-spin" />
            <KeyRound v-else class="size-4" />
            Cambiar en la OLT
          </button>
        </div>
      </form>

      <aside class="space-y-6">
        <section v-if="job" class="card p-5" aria-live="polite">
          <h2 class="mb-3 font-semibold">{{ running ? 'Cambio en curso' : 'Resultado' }}</h2>
          <JobProgress :job="job" />
          <p v-if="followError" class="mt-2 text-xs text-muted">Reintentando: {{ followError }}</p>
        </section>

        <section v-if="history.data.value?.length" class="card">
          <h2 class="border-b border-line px-5 py-3 font-semibold">Cambios anteriores</h2>
          <ul class="divide-y divide-line">
            <li
              v-for="item in history.data.value.slice(0, 5)"
              :key="item.id"
              class="flex items-center gap-3 px-5 py-3"
            >
              <span class="min-w-0 flex-1 text-sm">
                <span class="block">{{ formatDateTime(item.created_at) }}</span>
                <span v-if="item.username" class="text-xs text-muted">
                  Usuario {{ item.username }}
                </span>
              </span>
              <StatusPill :tone="JOB_STATUS[item.status].tone">
                {{ JOB_STATUS[item.status].label }}
              </StatusPill>
            </li>
          </ul>
        </section>
      </aside>
    </div>
  </template>
</template>
