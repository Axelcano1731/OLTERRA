import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// En desarrollo la API corre aparte (uvicorn en :8000). El proxy deja la interfaz igual
// que en producción, donde Caddy sirve las dos bajo el mismo dominio: sin CORS.
const api = process.env.OLTERRA_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: { '/v1': api, '/health': api, '/docs': api, '/openapi.json': api },
  },
})
