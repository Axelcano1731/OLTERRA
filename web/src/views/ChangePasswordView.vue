<script setup lang="ts">
import { LoaderCircle, LockKeyhole, LogOut } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { changePassword } from '@/api'
import AlertBox from '@/components/AlertBox.vue'
import Logo from '@/components/Logo.vue'
import { errorText } from '@/lib/useAsync'
import { mustChangePassword, refreshMe, session, signOut } from '@/session'

const router = useRouter()

const MIN_LENGTH = 10 // igual que security/passwords.py

const current = ref('')
const next = ref('')
const repeat = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const submitted = ref(false)

const problem = computed(() => {
  if (next.value.length < MIN_LENGTH) return `Al menos ${MIN_LENGTH} caracteres.`
  if (next.value !== repeat.value) return 'Las dos no coinciden.'
  if (next.value === current.value) return 'Tiene que ser distinta de la actual.'
  return null
})

async function submit(): Promise<void> {
  submitted.value = true
  error.value = null
  if (!current.value || problem.value) return
  busy.value = true
  try {
    await changePassword(current.value, next.value)
    current.value = next.value = repeat.value = ''
    await refreshMe()
    await router.replace({ name: 'panel' })
  } catch (caught) {
    error.value = errorText(caught)
  } finally {
    busy.value = false
  }
}

async function leave(): Promise<void> {
  await signOut()
  await router.replace({ name: 'connect' })
}
</script>

<template>
  <div class="flex min-h-dvh items-center justify-center bg-page p-6">
    <div class="w-full max-w-sm">
      <div class="mb-8 text-ink"><Logo /></div>
      <div class="flex items-center gap-2">
        <LockKeyhole class="size-5 text-accent" />
        <h1 class="text-2xl font-semibold tracking-tight">
          {{ mustChangePassword ? 'Elige tu contraseña' : 'Cambiar contraseña' }}
        </h1>
      </div>
      <p class="mt-2 text-sm text-muted">
        <template v-if="mustChangePassword">
          Hola {{ session.me?.user?.display_name ?? '' }}. Entraste con una contraseña inicial:
          elige una tuya para seguir. Olterra maneja la OLT de tus clientes, así que usa una que
          nadie adivine.
        </template>
        <template v-else>Las demás sesiones abiertas con tu usuario se cerrarán.</template>
      </p>

      <form class="mt-6 space-y-4" novalidate @submit.prevent="submit">
        <div>
          <label for="current" class="label">Contraseña actual</label>
          <input
            id="current"
            v-model="current"
            type="password"
            class="input"
            autocomplete="current-password"
            required
          />
        </div>
        <div>
          <label for="next" class="label">Contraseña nueva</label>
          <input
            id="next"
            v-model="next"
            type="password"
            class="input"
            autocomplete="new-password"
            :aria-invalid="submitted && !!problem"
            required
          />
          <p class="hint">Al menos {{ MIN_LENGTH }} caracteres. Una frase corta sirve.</p>
        </div>
        <div>
          <label for="repeat" class="label">Repítela</label>
          <input
            id="repeat"
            v-model="repeat"
            type="password"
            class="input"
            autocomplete="new-password"
            required
          />
          <p v-if="submitted && problem" class="hint text-danger">{{ problem }}</p>
        </div>

        <AlertBox v-if="error" tone="danger">{{ error }}</AlertBox>

        <button type="submit" class="btn-primary w-full" :disabled="busy">
          <LoaderCircle v-if="busy" class="size-4 animate-spin" />
          Guardar y entrar
        </button>
      </form>

      <button
        type="button"
        class="mt-6 inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink"
        @click="leave"
      >
        <LogOut class="size-3.5" />
        Salir
      </button>
    </div>
  </div>
</template>
