# Interfaz web

> Última actualización: 2026-10-01

La interfaz vive en `web/`: Vue 3 + TypeScript + Vite + Tailwind 4, la misma familia que
Converza (barra lateral azul noche, Inter) con acento propio. Es una SPA que habla con la API
por HTTP; no tiene lógica de negocio propia.

## Pantallas

| Ruta | Pantalla | Usa |
|---|---|---|
| `/conectar` | Entrar con la llave de API del ISP | `GET /v1/me` |
| `/` | Panel: cuántas OLT, routers y la última conciliación; primeros pasos; estado de la plataforma | `/health`, listas |
| `/olts`, `/olts/nueva` | OLT del ISP y alta (credenciales cifradas, nunca se vuelven a mostrar) | `GET/POST /v1/olts` |
| `/olts/:id` | Detalle: consultas de solo lectura con atajos, resultado interpretado, historial | `/v1/olts/{id}/commands`, `/queries`, `/plans`, `/v1/plans/{id}` |
| `/tunel` | MikroTik en el túnel: alta y rotación de llaves; el script se muestra una sola vez | `/v1/tunnel/routers` |
| `/conciliacion`, `/conciliacion/nueva`, `/conciliacion/:id` | Historial, nueva (archivos o demo) y detalle con filtros y CSV | `/v1/reconciliations` |

En la barra lateral se ven, sin abrirse, los módulos que vienen según el plan (autorizar ONU,
monitoreo, mapa FTTH).

## Cómo habla con la API

- **Mismo dominio, sin CORS.** En producción Caddy sirve la interfaz y pasa `/v1`, `/health`,
  `/docs` y `/openapi.json` a la API. En desarrollo hace lo mismo el proxy de Vite.
- **Tipos a mano.** `web/src/api/types.ts` refleja `src/olterra/api/schemas.py`: si cambia un
  esquema, cambia ahí en el mismo PR.
- **Errores.** El cliente (`web/src/api/client.ts`) muestra el `detail` de FastAPI tal cual
  (ya viene en español) y arma un texto legible con los errores de validación. Un 401 cierra
  la sesión y vuelve a `/conectar`.
- **Consultas a la OLT.** `POST /queries` responde 202 con el id del plan; la interfaz pregunta
  por el plan cada 1 a 3 s hasta que el ejecutor devuelve el resultado, y avisa si tarda.

## Sesión (fase 0)

No hay usuarios todavía: falta decidir el proveedor de identidad común a ISPWatch, Converza y
Olterra (ARQUITECTURA, A.10). Mientras tanto se entra con la llave de API del ISP:

- Queda en `sessionStorage` (se olvida al cerrar la pestaña) o, si se marca "recordar", en
  `localStorage`.
- La llave da todo lo que sus permisos (`scopes`) dan. La interfaz oculta lo que la llave no
  puede hacer, pero quien manda es la API.
- Cuando haya usuarios, la sesión pasa a cookie `HttpOnly` y llegan los roles y el 2FA del
  plan. Este es el punto más débil de hoy: la llave en el navegador.

## Seguridad

- Nada de `v-html` (regla de ESLint): todo texto que llega de la API se pinta escapado.
- La CSP la pone el servidor (Caddy): solo scripts, estilos y fuentes del mismo origen. La
  fuente Inter va dentro del build; no hay llamadas a terceros.
- El script del túnel trae la llave privada del router: vive solo en memoria mientras se
  muestra y no se guarda en el navegador.
- Las credenciales de la OLT solo viajan al crearla; la API nunca las devuelve.

## Desarrollo

```powershell
cd web
npm install
npm run dev            # http://localhost:5173, con la API en :8000 (o OLTERRA_API_URL)
npm run lint           # ESLint (con reglas de tipos)
npm run format:check   # Prettier (npm run format para aplicarlo)
npm run typecheck      # vue-tsc
npm test               # Vitest
npm run build          # dist/ para producción
```

Requiere Node 22.12 o superior (CI y la imagen usan Node 24).

## Convenciones

- Identificadores en inglés; textos en español, cortos y en segunda persona ("Agrega tu OLT").
- Colores como variables en `web/src/styles.css`; el modo oscuro sigue al sistema y solo
  redefine esas variables.
- Piezas reutilizables en `web/src/components`; lógica pura (con pruebas) en `web/src/lib`.
- Sin librería de componentes: Tailwind y unos pocos componentes propios alcanzan por ahora.

## Lo que viene

- Mapa FTTH (fase 2): MapLibre con teselas licenciadas (MapTiler o Esri), no Google.
- Autorización de ONU y monitoreo (fase 1), con las escrituras a la OLT.
- Login de usuarios cuando se decida el proveedor de identidad.
