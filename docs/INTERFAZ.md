# Interfaz web

> Última actualización: 2026-10-01

La interfaz vive en `web/`: Vue 3 + TypeScript + Vite + Tailwind 4, la misma familia que
Converza (barra lateral azul noche, Inter) con acento propio. Es una SPA que habla con la API
por HTTP; no tiene lógica de negocio propia.

## Pantallas

| Ruta | Pantalla | Usa |
|---|---|---|
| `/entrar` (antes `/conectar`) | Entrar con usuario y contraseña (o con una llave de API, para integraciones) | `POST /v1/auth/login`, `GET /v1/me` |
| `/cambiar-clave` | Cambiar la contraseña; obligatoria con la inicial | `POST /v1/auth/password` |
| `/aprovisionar` | Entrada del menú: con una sola OLT va directo a su pantalla; con varias, se elige | `GET /v1/olts` |
| `/` | Panel: cuántas OLT, routers y la última conciliación; primeros pasos; estado de la plataforma | `/health`, listas |
| `/olts`, `/olts/nueva` | OLT del ISP con su estado (**Sin consultar**, **Responde** o **Sin respuesta**: lo actualiza cada consulta o escritura), con **Consultar**, **Aprovisionar**, **Editar** y **Eliminar** en cada fila (el borrado pide confirmación y dice si hay que rotar el MikroTik), y alta (credenciales cifradas, nunca se vuelven a mostrar). Con una OLT nueva se dejan usuario y clave vacíos y el servidor usa los de fábrica (aviso para cambiarlos); la clave de enable la pone el cliente | `GET/POST /v1/olts`, `GET /v1/olts/defaults`, `DELETE /v1/olts/{id}` |
| `/olts/:id/editar` | Corregir modelo, firmware, IP, puertos y credenciales (incluida la de enable); las claves se vuelven a cifrar y la bitácora solo anota cuáles cambiaron | `PATCH /v1/olts/{id}` |
| `/olts/:id` | Detalle: consultas de solo lectura con atajos, resultado interpretado, historial | `/v1/olts/{id}/commands`, `/queries`, `/plans`, `/v1/plans/{id}` |
| `/olts/:id/aprovisionar` | Al abrir busca sola en todos los PON (ONU nuevas) y lee la lista de clientes. **Autorizar** pide solo nombre del cliente (tildes y espacios se arreglan), plan, PPPoE y WiFi: el resto lo hace el trabajo de alta y se ven los pasos con ✓. **Últimas altas** guarda el resultado. Cada cliente (buscador por nombre) tiene Internet y WiFi, Reiniciar, Copiar como plan y Desautorizar | `POST /v1/olts/{id}/onus/scan`, `/onus/authorize`, `/onus/configure`, `/onus/reboot`, `/onus/delete`, `/v1/provision-jobs/{id}`, `/v1/olts/{id}/provision-jobs` |
| `/plantillas`, `/plantillas/nueva`, `/plantillas/:id` | **Planes** (plantillas de aprovisionamiento): se copian de un cliente que ya navega ("Copiar como plan" en Aprovisionar abre esta pantalla y lee la ONU sola) o se llenan a mano; formulario para el caso de una VLAN y JSON para lo demás. **Gestión remota de la ONU**: firewall, ping y servicios abiertos desde internet (selección múltiple); avisa si se abre la web de la ONU | `/v1/provision-templates`, `/queries` |
| `/tunel` | MikroTik en el túnel: alta con su versión de RouterOS (7 → WireGuard, 6 → SSTP), rotación y cambio de versión; el script se muestra una sola vez | `/v1/tunnel/routers` |
| `/conciliacion`, `/conciliacion/nueva`, `/conciliacion/:id` | Historial, nueva (archivos o demo) y detalle con filtros y CSV | `/v1/reconciliations` |

En la barra lateral se ven, sin abrirse, los módulos que vienen según el plan (monitoreo,
mapa FTTH).

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

## Sesión

Se entra con **usuario y contraseña** (`/entrar`). La llave de API sigue sirviendo para
integraciones (n8n, scripts): en la misma pantalla, "Entrar con una llave de API".

- Los usuarios los crea el administrador (`./olterra.sh usuario`, ver DESPLIEGUE.md) con una
  contraseña inicial que **hay que cambiar al entrar**: mientras tanto la API solo deja ver quién
  es, cambiarla o salir, y la interfaz manda a `/cambiar-clave`.
- La sesión es un token `ols_…` (mismo diseño que las llaves: el tenant va adentro y se valida
  bajo RLS). Dura 12 horas, o 30 días con "mantener la sesión"; queda en `sessionStorage` o
  `localStorage` como la llave. Salir la cierra también en el servidor.
- Roles: `admin` (todo), `tecnico` (consultar y aprovisionar) y `lectura` (solo consultar).
- Contraseñas con scrypt; 5 intentos fallidos bloquean la cuenta 15 minutos y hay un tope de
  intentos por IP. Usuario inexistente y contraseña equivocada responden igual.
- Pendiente: cookie `HttpOnly` en vez de almacenamiento del navegador, 2FA e identidad común con
  ISPWatch y Converza (ARQUITECTURA, A.10).

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
- Monitoreo (fase 1); acciones masivas y mover ONU de puerto.
- Login de usuarios cuando se decida el proveedor de identidad.
