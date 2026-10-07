<script setup lang="ts">
import { LoaderCircle, Pencil, Play, RadioTower, RefreshCw, Terminal } from '@lucide/vue'
import { computed, nextTick, ref, useTemplateRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getOlt, listCommands, listPlans, queryOlt, type CommandScope } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import DeleteOlt from '@/components/DeleteOlt.vue'
import LoadingBlock from '@/components/LoadingBlock.vue'
import PageHeader from '@/components/PageHeader.vue'
import PlanResult from '@/components/PlanResult.vue'
import StatusPill from '@/components/StatusPill.vue'
import { formatDateTime, timeAgo } from '@/lib/format'
import { oltStatus, planStatus } from '@/lib/labels'
import { parseOnus, parsePons, TargetError } from '@/lib/targets'
import { errorText, useAsync } from '@/lib/useAsync'
import { usePlan } from '@/lib/usePlan'
import { can } from '@/session'

const props = defineProps<{ id: string }>()
const route = useRoute()
const router = useRouter()
const deleteError = ref<string | null>(null)

const olt = useAsync(() => getOlt(props.id))
const commands = useAsync(() => listCommands(props.id))
const history = useAsync(() => listPlans(props.id))

const justCreated = computed(() => route.query.nueva === '1')
const justEdited = computed(() => route.query.editada === '1')
const usedFactory = computed(() => route.query.fabrica === '1')

const GROUPS: { scope: CommandScope; title: string; text: string }[] = [
  { scope: 'olt', title: 'Equipo', text: 'Una vez por consulta.' },
  { scope: 'pon', title: 'Por puerto PON', text: 'Se repiten en cada PON que indiques.' },
  { scope: 'onu', title: 'Por ONU', text: 'Se repiten en cada ONU que indiques.' },
]

const PRESETS = [
  {
    label: 'Estado del equipo',
    keys: ['system.version', 'system.running_time', 'system.cpu', 'system.memory', 'system.fan'],
  },
  { label: 'ONUs y potencias', keys: ['onu.list', 'onu.rx_power_all'] },
  { label: 'ONUs sin autorizar', keys: ['onu.autofind'] },
]

const selected = ref<string[]>([])
const ponText = ref('1')
const onuText = ref('1:1')

const grouped = computed(() =>
  GROUPS.map((group) => ({
    ...group,
    commands: (commands.data.value ?? []).filter((command) => command.scope === group.scope),
  })).filter((group) => group.commands.length > 0),
)

const anyVerified = computed(() => (commands.data.value ?? []).some((command) => command.verified))

const needs = computed(() => {
  const scopes = new Set(
    (commands.data.value ?? [])
      .filter((command) => selected.value.includes(command.key))
      .map((command) => command.scope),
  )
  return { pon: scopes.has('pon'), onu: scopes.has('onu') }
})

const targets = computed(() => {
  try {
    const pon = needs.value.pon ? parsePons(ponText.value) : []
    const onu = needs.value.onu ? parseOnus(onuText.value) : []
    if (needs.value.pon && pon.length === 0) return { pon, onu, error: 'Indica al menos un PON.' }
    if (needs.value.onu && onu.length === 0) {
      return { pon, onu, error: 'Indica al menos una ONU como PON:ONU.' }
    }
    return { pon, onu, error: null }
  } catch (caught) {
    const message = caught instanceof TargetError ? caught.message : 'Revisa los PON y las ONU.'
    return { pon: [], onu: [], error: message }
  }
})

function applyPreset(keys: string[]): void {
  const available = new Set((commands.data.value ?? []).map((command) => command.key))
  selected.value = keys.filter((key) => available.has(key))
}

const { plan, error: planError, waiting, slow, follow } = usePlan()
const sending = ref(false)
const sendError = ref<string | null>(null)
const resultBox = useTemplateRef<HTMLElement>('result')

async function showResult(planId: string): Promise<void> {
  const done = follow(planId) // marca "esperando" de inmediato: la sección ya se pinta
  await nextTick()
  resultBox.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  await done
}

async function send(): Promise<void> {
  sendError.value = null
  if (selected.value.length === 0 || targets.value.error) return
  sending.value = true
  try {
    // En el orden del catálogo: así el resultado sale agrupado igual que la lista.
    const ordered = (commands.data.value ?? [])
      .filter((command) => selected.value.includes(command.key))
      .map((command) => command.key)
    const queued = await queryOlt(props.id, {
      commands: ordered,
      pon: targets.value.pon,
      onu: targets.value.onu,
    })
    void history.reload()
    await showResult(queued.plan_id)
  } catch (caught) {
    sendError.value = errorText(caught)
  } finally {
    sending.value = false
  }
}

// Cuando el plan termina, el historial muestra su estado nuevo.
watch(waiting, (now, before) => {
  if (before && !now) void history.reload()
})
</script>

<template>
  <LoadingBlock v-if="olt.loading.value && !olt.data.value" />
  <AlertBox v-else-if="olt.error.value" tone="danger">{{ olt.error.value }}</AlertBox>

  <template v-else-if="olt.data.value">
    <PageHeader :title="olt.data.value.name" :back="{ name: 'olts' }" back-label="OLT">
      <template #meta>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusPill :tone="oltStatus(olt.data.value.status).tone">
            {{ oltStatus(olt.data.value.status).label }}
          </StatusPill>
          <span>{{ olt.data.value.model ?? 'Modelo sin indicar' }}</span>
          <span v-if="olt.data.value.firmware">· {{ olt.data.value.firmware }}</span>
        </div>
      </template>
      <template v-if="can('olt:write')" #actions>
        <RouterLink
          v-if="can('onu:write')"
          :to="{ name: 'olt-provision', params: { id } }"
          class="btn-primary"
        >
          <RadioTower class="size-4" />
          Aprovisionar
        </RouterLink>
        <RouterLink :to="{ name: 'olt-edit', params: { id } }" class="btn-secondary">
          <Pencil class="size-4" />
          Editar
        </RouterLink>
        <DeleteOlt
          :olt="olt.data.value"
          @deleted="
            (gone) =>
              router.push({
                name: 'olts',
                query: { borrada: gone.name, router: gone.router_id ? '1' : '0' },
              })
          "
          @failed="deleteError = $event"
        />
      </template>
    </PageHeader>

    <AlertBox v-if="deleteError" tone="danger" class="mb-6">{{ deleteError }}</AlertBox>

    <AlertBox v-if="justEdited" tone="success" title="OLT actualizada" class="mb-6">
      Los cambios quedaron guardados.
      <template v-if="olt.data.value.router_id">
        Si cambiaste su IP, rota las llaves de su MikroTik en
        <RouterLink :to="{ name: 'tunnel' }" class="font-medium underline">Túnel</RouterLink>
        para publicarla.
      </template>
    </AlertBox>

    <AlertBox
      v-if="justCreated && usedFactory"
      tone="warning"
      title="Se usó la clave de fábrica"
      class="mb-6"
    >
      Cambia la clave de acceso en la OLT y luego actualízala aquí con Editar: la de fábrica es
      pública.
    </AlertBox>

    <AlertBox v-if="justCreated" tone="success" title="OLT agregada" class="mb-6">
      Sus credenciales quedaron cifradas con la llave de tu ISP.
      <template v-if="olt.data.value.nat_ip">
        Para que tu MikroTik la publique en el túnel, ve a
        <RouterLink :to="{ name: 'tunnel' }" class="font-medium underline">Túnel</RouterLink>, rota
        las llaves de su router y pega el script nuevo: ya incluye esta OLT.
      </template>
      <template v-else>
        Prueba una consulta de solo lectura para confirmar que Olterra llega.
      </template>
    </AlertBox>

    <dl class="card mb-6 grid grid-cols-2 gap-x-6 gap-y-4 p-5 text-sm sm:grid-cols-4">
      <div>
        <dt class="text-muted">IP en tu red</dt>
        <dd class="mt-0.5 font-mono">{{ olt.data.value.real_ip ?? '—' }}</dd>
      </div>
      <div>
        <dt class="text-muted">IP en el túnel</dt>
        <dd class="mt-0.5 font-mono">{{ olt.data.value.nat_ip ?? 'Directa' }}</dd>
      </div>
      <div>
        <dt class="text-muted">Puertos</dt>
        <dd class="mt-0.5">
          SSH {{ olt.data.value.ssh_port }} · SNMP {{ olt.data.value.snmp_port }}
        </dd>
      </div>
      <div>
        <dt class="text-muted">Alta</dt>
        <dd class="mt-0.5">{{ formatDateTime(olt.data.value.created_at) }}</dd>
      </div>
    </dl>

    <div class="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div class="min-w-0 space-y-6">
        <section class="card p-5 sm:p-6" aria-labelledby="consultar">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="consultar" class="font-semibold">Consultar la OLT</h2>
              <p class="mt-1 text-sm text-muted">
                Solo lectura: nada de esto cambia la configuración.
              </p>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="preset in PRESETS"
                :key="preset.label"
                type="button"
                class="btn-secondary px-2.5 py-1 text-xs"
                @click="applyPreset(preset.keys)"
              >
                {{ preset.label }}
              </button>
            </div>
          </div>

          <AlertBox v-if="commands.data.value && !anyVerified" tone="info" class="mt-4">
            Ningún comando está verificado todavía contra una OLT de laboratorio. Si la salida no se
            reconoce, verás el texto tal cual.
          </AlertBox>

          <LoadingBlock v-if="commands.loading.value && !commands.data.value" />
          <AlertBox v-else-if="commands.error.value" tone="danger" class="mt-4">
            {{ commands.error.value }}
          </AlertBox>

          <div v-else class="mt-5 space-y-5">
            <fieldset v-for="group in grouped" :key="group.scope">
              <legend class="text-sm font-medium">
                {{ group.title }}
                <span class="font-normal text-muted">· {{ group.text }}</span>
              </legend>
              <div class="mt-2 grid gap-1.5 sm:grid-cols-2">
                <label
                  v-for="command in group.commands"
                  :key="command.key"
                  :class="[
                    'flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 transition-colors',
                    selected.includes(command.key)
                      ? 'border-accent/60 bg-accent-soft'
                      : 'border-line hover:bg-subtle',
                  ]"
                  :title="command.notes || undefined"
                >
                  <input
                    v-model="selected"
                    type="checkbox"
                    :value="command.key"
                    class="mt-0.5 size-4 shrink-0 accent-accent"
                  />
                  <span class="min-w-0">
                    <span class="flex flex-wrap items-center gap-1.5">
                      <span class="font-mono text-xs font-medium">{{ command.key }}</span>
                      <StatusPill v-if="command.parsed" tone="accent">datos</StatusPill>
                      <StatusPill v-if="command.verified" tone="success">verificado</StatusPill>
                    </span>
                    <span class="block truncate font-mono text-[11px] text-muted">
                      {{ command.command }}
                    </span>
                  </span>
                </label>
              </div>
            </fieldset>

            <div v-if="needs.pon || needs.onu" class="grid gap-4 sm:grid-cols-2">
              <div v-if="needs.pon">
                <label for="pons" class="label">PON</label>
                <input
                  id="pons"
                  v-model="ponText"
                  class="input font-mono"
                  placeholder="1, 2, 4-6"
                />
                <p class="hint">Del 1 al 16. Separa con comas; los rangos van con guion.</p>
              </div>
              <div v-if="needs.onu">
                <label for="onus" class="label">ONU (PON:ONU)</label>
                <input
                  id="onus"
                  v-model="onuText"
                  class="input font-mono"
                  placeholder="1:5, 2:1-4"
                />
                <p class="hint">Por ejemplo 1:5 es la ONU 5 del PON 1.</p>
              </div>
            </div>

            <AlertBox v-if="targets.error && selected.length" tone="warning">
              {{ targets.error }}
            </AlertBox>
            <AlertBox v-if="sendError" tone="danger" title="No se pudo enviar la consulta">
              {{ sendError }}
            </AlertBox>

            <div class="flex items-center justify-between gap-3 border-t border-line pt-4">
              <p class="text-sm text-muted">
                {{ selected.length ? `${selected.length} seleccionados` : 'Elige qué leer' }}
              </p>
              <button
                type="button"
                class="btn-primary"
                :disabled="sending || waiting || selected.length === 0 || !!targets.error"
                @click="send"
              >
                <LoaderCircle v-if="sending || waiting" class="size-4 animate-spin" />
                <Play v-else class="size-4" />
                Consultar
              </button>
            </div>
          </div>
        </section>

        <section
          v-if="plan || waiting || planError"
          ref="result"
          class="card scroll-mt-20 p-5 sm:p-6"
          aria-labelledby="resultado"
        >
          <h2 id="resultado" class="mb-4 font-semibold">Resultado</h2>
          <AlertBox v-if="planError" tone="danger">{{ planError }}</AlertBox>
          <div v-else-if="waiting" class="space-y-3">
            <div class="flex items-center gap-2 text-sm text-muted">
              <LoaderCircle class="size-4 animate-spin" />
              En cola: esperando al ejecutor…
            </div>
            <AlertBox v-if="slow" tone="warning">
              Está tardando. Revisa que el ejecutor esté corriendo y que la OLT responda en su IP.
            </AlertBox>
          </div>
          <PlanResult v-else-if="plan" :plan="plan" />
        </section>
      </div>

      <aside class="card p-5" aria-labelledby="historial">
        <div class="flex items-center justify-between">
          <h2 id="historial" class="font-semibold">Consultas recientes</h2>
          <button
            type="button"
            class="btn-ghost p-1.5"
            aria-label="Actualizar"
            @click="history.reload"
          >
            <RefreshCw :class="['size-4', history.loading.value && 'animate-spin']" />
          </button>
        </div>
        <p v-if="history.error.value" class="mt-3 text-sm text-danger">
          {{ history.error.value }}
        </p>
        <p
          v-else-if="history.data.value && history.data.value.length === 0"
          class="mt-3 flex items-center gap-2 text-sm text-muted"
        >
          <Terminal class="size-4" />
          Todavía no hay consultas.
        </p>
        <ul v-else class="mt-3 -mx-2 space-y-0.5">
          <li v-for="item in history.data.value ?? []" :key="item.plan_id">
            <button
              type="button"
              :class="[
                'w-full rounded-lg px-2 py-2 text-left hover:bg-subtle',
                plan?.plan_id === item.plan_id && 'bg-subtle',
              ]"
              @click="showResult(item.plan_id)"
            >
              <span class="flex items-center justify-between gap-2">
                <span class="text-xs text-muted" :title="formatDateTime(item.created_at)">
                  {{ timeAgo(item.created_at) }}
                </span>
                <StatusPill :tone="planStatus(item.status).tone">
                  {{ planStatus(item.status).label }}
                </StatusPill>
              </span>
              <span class="mt-1 block truncate font-mono text-xs">
                {{ item.commands.join(', ') }}
              </span>
            </button>
          </li>
        </ul>
      </aside>
    </div>
  </template>
</template>
