import '@fontsource-variable/inter'
import './styles.css'

import { createApp } from 'vue'

import { configureClient } from './api/client'
import App from './App.vue'
import { router } from './router'
import { disconnect, session } from './session'

configureClient({
  getKey: () => session.key,
  onUnauthorized: () => {
    disconnect()
    void router.replace({ name: 'connect', query: { motivo: 'expirada' } })
  },
})

createApp(App).use(router).mount('#app')
