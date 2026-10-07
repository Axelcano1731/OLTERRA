<script setup lang="ts">
import { computed } from 'vue'

import type { ProvisionTemplate } from '@/api'
import type { CustomerService } from '@/lib/customer'
import { oltLabel } from '@/lib/oltText'

// Usuario y clave PPPoE y WiFi del cliente: lo que pide el plan elegido, y nada más.

const props = defineProps<{
  plan: ProvisionTemplate | undefined
  showErrors: boolean
  idPrefix: string
}>()
const model = defineModel<CustomerService>({ required: true })

const PPPOE_USER = /^[A-Za-z0-9_.@-]{1,64}$/
const CLI_SECRET = (low: number) => new RegExp(`^[!-~]{${low},63}$`)

const wifiPreview = computed(() => oltLabel(model.value.wifiName, 32))

const problems = computed(() => {
  const found: Record<string, string> = {}
  const plan = props.plan?.body
  if (plan?.wan) {
    if (!PPPOE_USER.test(model.value.pppoeUser)) {
      found.pppoeUser = 'El usuario PPPoE va sin espacios (como está en el MikroTik).'
    }
    if (!CLI_SECRET(1).test(model.value.pppoePassword) || model.value.pppoePassword.includes('?')) {
      found.pppoePassword = 'La clave va sin espacios ni signo de pregunta.'
    }
  }
  if (plan?.wifi && (model.value.wifiName || model.value.wifiKey)) {
    if (!wifiPreview.value) found.wifiName = 'Escribe un nombre para el WiFi.'
    if (!CLI_SECRET(8).test(model.value.wifiKey) || model.value.wifiKey.includes('?')) {
      found.wifiKey = 'De 8 a 63 caracteres, sin espacios ni signo de pregunta.'
    }
  }
  return found
})

defineExpose({ valid: computed(() => Object.keys(problems.value).length === 0) })

function show(field: string): string | undefined {
  return props.showErrors ? problems.value[field] : undefined
}
</script>

<template>
  <template v-if="plan?.body.wan">
    <div>
      <label :for="`${idPrefix}-pppoe-user`" class="label">Usuario PPPoE</label>
      <input
        :id="`${idPrefix}-pppoe-user`"
        v-model="model.pppoeUser"
        class="input"
        autocomplete="off"
        autocapitalize="none"
        spellcheck="false"
        :aria-invalid="!!show('pppoeUser')"
      />
      <p v-if="show('pppoeUser')" class="hint text-danger">{{ show('pppoeUser') }}</p>
    </div>
    <div>
      <label :for="`${idPrefix}-pppoe-password`" class="label">Clave PPPoE</label>
      <input
        :id="`${idPrefix}-pppoe-password`"
        v-model="model.pppoePassword"
        type="password"
        class="input"
        autocomplete="new-password"
        :aria-invalid="!!show('pppoePassword')"
      />
      <p v-if="show('pppoePassword')" class="hint text-danger">{{ show('pppoePassword') }}</p>
    </div>
  </template>
  <template v-if="plan?.body.wifi">
    <div>
      <label :for="`${idPrefix}-wifi-name`" class="label">
        Nombre del WiFi <span class="text-muted">(opcional)</span>
      </label>
      <input
        :id="`${idPrefix}-wifi-name`"
        v-model="model.wifiName"
        class="input"
        autocomplete="off"
        maxlength="40"
        :aria-invalid="!!show('wifiName')"
      />
      <p v-if="show('wifiName')" class="hint text-danger">{{ show('wifiName') }}</p>
      <p v-else-if="model.wifiName && wifiPreview !== model.wifiName" class="hint">
        Quedará como <b class="font-mono">{{ wifiPreview }}</b> (la OLT no acepta espacios ni
        tildes).
      </p>
    </div>
    <div>
      <label :for="`${idPrefix}-wifi-key`" class="label">
        Clave del WiFi <span class="text-muted">(opcional)</span>
      </label>
      <input
        :id="`${idPrefix}-wifi-key`"
        v-model="model.wifiKey"
        type="password"
        class="input"
        autocomplete="new-password"
        :aria-invalid="!!show('wifiKey')"
      />
      <p v-if="show('wifiKey')" class="hint text-danger">{{ show('wifiKey') }}</p>
    </div>
  </template>
</template>
