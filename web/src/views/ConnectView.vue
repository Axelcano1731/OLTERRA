<script setup lang="ts">
import { CircleCheck, Eye, EyeOff, KeyRound, LoaderCircle, LogIn } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AlertBox from '@/components/AlertBox.vue'
import Logo from '@/components/Logo.vue'
import { looksLikeApiKey } from '@/lib/apiKey'
import { errorText } from '@/lib/useAsync'
import { connect, signIn } from '@/session'

const route = useRoute()
const router = useRouter()

// Usuario y contraseña; la llave de API queda para integraciones (n8n, scripts).
const mode = ref<'user' | 'key'>('user')
const username = ref('')
const password = ref('')
const key = ref('')
const remember = ref(false)
const showSecret = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

const expired = computed(() => route.query.motivo === 'expirada')
const badFormat = computed(() => key.value.trim() !== '' && !looksLikeApiKey(key.value))

const FEATURES = [
  'Autoriza una ONU nueva en un clic: Olterra hace el resto',
  'Cada ONU con su cliente, su señal y su estado',
  'Conciliación OLT ↔ MikroTik ↔ CRM con acción sugerida',
]

/** Solo rutas internas: un "volver" con dominio (//otro.sitio) no se sigue. */
function returnPath(): string {
  const back = route.query.volver
  return typeof back === 'string' && back.startsWith('/') && !back.startsWith('//') ? back : '/'
}

async function submit(): Promise<void> {
  error.value = null
  if (mode.value === 'user' && (!username.value.trim() || !password.value)) {
    error.value = 'Escribe tu usuario y tu contraseña.'
    return
  }
  if (mode.value === 'key' && !looksLikeApiKey(key.value)) {
    error.value = 'Eso no parece una llave de Olterra. Revisa que la hayas copiado completa.'
    return
  }
  busy.value = true
  try {
    if (mode.value === 'user') {
      const me = await signIn(username.value, password.value, remember.value)
      password.value = ''
      if (me.user?.must_change_password) {
        await router.replace({ name: 'change-password' })
        return
      }
    } else {
      await connect(key.value, remember.value)
    }
    await router.replace(returnPath())
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

function switchMode(): void {
  mode.value = mode.value === 'user' ? 'key' : 'user'
  error.value = null
  showSecret.value = false
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
        <p class="mt-1 text-sm text-muted">
          {{ mode === 'user' ? 'Con tu usuario de Olterra.' : 'Con la llave de API de tu ISP.' }}
        </p>

        <AlertBox v-if="expired && !error" tone="warning" class="mt-6">
          La sesión se cerró: venció o ya no es válida. Vuelve a entrar.
        </AlertBox>

        <form class="mt-6 space-y-4" novalidate @submit.prevent="submit">
          <template v-if="mode === 'user'">
            <div>
              <label for="username" class="label">Usuario</label>
              <input
                id="username"
                v-model="username"
                class="input"
                autocomplete="username"
                autocapitalize="none"
                spellcheck="false"
                required
              />
            </div>
            <div>
              <label for="password" class="label">Contraseña</label>
              <div class="relative">
                <input
                  id="password"
                  v-model="password"
                  :type="showSecret ? 'text' : 'password'"
                  class="input pr-10"
                  autocomplete="current-password"
                  required
                />
                <button
                  type="button"
                  class="absolute inset-y-0 right-0 grid w-10 place-items-center text-muted hover:text-ink"
                  :aria-label="showSecret ? 'Ocultar la contraseña' : 'Mostrar la contraseña'"
                  @click="showSecret = !showSecret"
                >
                  <EyeOff v-if="showSecret" class="size-4" />
                  <Eye v-else class="size-4" />
                </button>
              </div>
            </div>
          </template>
          <div v-else>
            <label for="api-key" class="label">Llave de API</label>
            <div class="relative">
              <input
                id="api-key"
                v-model="key"
                :type="showSecret ? 'text' : 'password'"
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
                :aria-label="showSecret ? 'Ocultar la llave' : 'Mostrar la llave'"
                @click="showSecret = !showSecret"
              >
                <EyeOff v-if="showSecret" class="size-4" />
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
              Mantener la sesión en este equipo
              <span class="block text-xs text-muted">
                Sin marcar, se cierra al cerrar la pestaña (y a las 12 horas).
              </span>
            </span>
          </label>

          <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>

          <button type="submit" class="btn-primary w-full" :disabled="busy">
            <LoaderCircle v-if="busy" class="size-4 animate-spin" />
            <LogIn v-else-if="mode === 'user'" class="size-4" />
            <KeyRound v-else class="size-4" />
            Entrar
          </button>
        </form>

        <button
          type="button"
          class="mt-8 text-xs text-muted underline-offset-2 hover:text-ink hover:underline"
          @click="switchMode"
        >
          {{
            mode === 'user'
              ? 'Entrar con una llave de API (integraciones)'
              : 'Entrar con usuario y contraseña'
          }}
        </button>
      </div>
    </section>
  </div>
</template>
