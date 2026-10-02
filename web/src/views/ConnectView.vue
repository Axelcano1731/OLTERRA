<script setup lang="ts">
import { CircleCheck, Eye, EyeOff, KeyRound, LoaderCircle } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AlertBox from '@/components/AlertBox.vue'
import Logo from '@/components/Logo.vue'
import { looksLikeApiKey } from '@/lib/apiKey'
import { errorText } from '@/lib/useAsync'
import { connect } from '@/session'

const route = useRoute()
const router = useRouter()

const key = ref('')
const remember = ref(false)
const showKey = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

const expired = computed(() => route.query.motivo === 'expirada')
const badFormat = computed(() => key.value.trim() !== '' && !looksLikeApiKey(key.value))

const FEATURES = [
  'Conciliación OLT ↔ MikroTik ↔ CRM con acción sugerida',
  'Consultas de solo lectura a la OLT, sin abrir puertos',
  'Túnel WireGuard listo para pegar en tu MikroTik',
]

/** Solo rutas internas: un "volver" con dominio (//otro.sitio) no se sigue. */
function returnPath(): string {
  const back = route.query.volver
  return typeof back === 'string' && back.startsWith('/') && !back.startsWith('//') ? back : '/'
}

async function submit(): Promise<void> {
  error.value = null
  if (!looksLikeApiKey(key.value)) {
    error.value = 'Eso no parece una llave de Olterra. Revisa que la hayas copiado completa.'
    return
  }
  busy.value = true
  try {
    await connect(key.value, remember.value)
    await router.replace(returnPath())
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
    <section
      class="relative hidden overflow-hidden bg-sidebar p-12 text-white lg:flex lg:flex-col lg:justify-between"
    >
      <svg
        class="pointer-events-none absolute -right-40 -bottom-40 size-[34rem] opacity-[0.07]"
        viewBox="0 0 200 200"
        aria-hidden="true"
      >
        <circle cx="100" cy="100" r="96" fill="none" stroke="white" stroke-width="2" />
        <circle cx="100" cy="100" r="70" fill="none" stroke="white" stroke-width="2" />
        <circle cx="100" cy="100" r="44" fill="none" stroke="white" stroke-width="2" />
        <circle cx="100" cy="100" r="16" fill="white" />
      </svg>
      <Logo />
      <div class="relative max-w-md">
        <h1 class="text-4xl leading-tight font-semibold tracking-tight">
          Tu OLT, tu fibra y tus clientes en un solo mapa.
        </h1>
        <p class="mt-4 text-sidebar-muted">
          Gestión de OLT VSOL amarrada a tu MikroTik y a tu CRM: cada ONU con su cliente, su caja
          NAP y su potencia.
        </p>
        <ul class="mt-8 space-y-3 text-sm text-sidebar-ink">
          <li v-for="feature in FEATURES" :key="feature" class="flex items-start gap-2.5">
            <CircleCheck class="mt-0.5 size-4 shrink-0 text-accent" />
            {{ feature }}
          </li>
        </ul>
      </div>
      <p class="relative text-xs text-sidebar-muted">Fase 0 · laboratorio y pilotos</p>
    </section>

    <section class="flex items-center justify-center p-6">
      <div class="w-full max-w-sm">
        <div class="mb-10 text-ink lg:hidden"><Logo /></div>
        <h2 class="text-2xl font-semibold tracking-tight">Entrar</h2>
        <p class="mt-1 text-sm text-muted">Pega la llave de API de tu ISP.</p>

        <AlertBox v-if="expired && !error" tone="warning" class="mt-6">
          La sesión se cerró porque la llave dejó de ser válida.
        </AlertBox>

        <form class="mt-6 space-y-4" novalidate @submit.prevent="submit">
          <div>
            <label for="api-key" class="label">Llave de API</label>
            <div class="relative">
              <input
                id="api-key"
                v-model="key"
                :type="showKey ? 'text' : 'password'"
                class="input pr-10 font-mono"
                autocomplete="off"
                spellcheck="false"
                placeholder="olt_…"
                :aria-invalid="badFormat"
                required
              />
              <button
                type="button"
                class="absolute inset-y-0 right-0 grid w-10 place-items-center text-muted hover:text-ink"
                :aria-label="showKey ? 'Ocultar la llave' : 'Mostrar la llave'"
                @click="showKey = !showKey"
              >
                <EyeOff v-if="showKey" class="size-4" />
                <Eye v-else class="size-4" />
              </button>
            </div>
            <p v-if="badFormat" class="hint text-warning">
              El formato es olt_&lt;tenant&gt;_&lt;id&gt;_&lt;secreto&gt;.
            </p>
          </div>

          <label class="flex items-start gap-2.5 text-sm">
            <input v-model="remember" type="checkbox" class="mt-0.5 size-4 accent-accent" />
            <span>
              Recordar en este equipo
              <span class="block text-xs text-muted">
                Sin marcar, la llave se olvida al cerrar la pestaña.
              </span>
            </span>
          </label>

          <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>

          <button type="submit" class="btn-primary w-full" :disabled="busy">
            <LoaderCircle v-if="busy" class="size-4 animate-spin" />
            <KeyRound v-else class="size-4" />
            Entrar
          </button>
        </form>

        <p class="mt-8 text-xs leading-relaxed text-muted">
          ¿No tienes llave? La crea el administrador de Olterra con
          <code class="code whitespace-nowrap">olterra-admin crear-llave</code>. Se muestra una sola
          vez.
        </p>
      </div>
    </section>
  </div>
</template>
