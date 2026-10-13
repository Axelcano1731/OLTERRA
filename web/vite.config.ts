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
  // El worker de MapLibre (mapa FTTH) es un módulo ES: se empaqueta igual y se sirve desde el
  // mismo origen, así la CSP no tiene que abrir blob: para scripts.
  worker: { format: 'es' },
  // MapLibre (~1 MB, 285 KB comprimido) va solo en el trozo de la pantalla del mapa, que se carga
  // al abrirla: el resto de la interfaz no lo descarga. El aviso queda para cualquier otro trozo.
  build: { chunkSizeWarningLimit: 1100 },
})
