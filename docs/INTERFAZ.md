# Interfaz web

> Última actualización: 2026-10-27

La interfaz vive en `web/`: Vue 3 + TypeScript + Vite + Tailwind 4, la misma familia que
Converza (barra lateral azul noche, Inter) con acento propio. Es una SPA que habla con la API
por HTTP; no tiene lógica de negocio propia.

## Pantallas

| Ruta | Pantalla | Usa |
|---|---|---|
| `/entrar` (antes `/conectar`) | Entrar con usuario y contraseña (o con una llave de API, para integraciones) | `POST /v1/auth/login`, `GET /v1/me` |
| `/cambiar-clave` | Cambiar la contraseña; obligatoria con la inicial | `POST /v1/auth/password` |
| `/aprovisionar` | Entrada del menú: con una sola OLT va directo a su pantalla; con varias, se elige. En HSGQ y BCD se ven las ONU nuevas y los clientes, y la pantalla dice que el alta se habilita al probarla con una ONU de prueba | `GET /v1/olts` |
| `/usuarios` | **Usuarios** (solo admin): el nombre del ISP y sus usuarios. Crear (Olterra genera una contraseña inicial que se muestra una vez y obliga a cambiarla), cambiar el rol, deshabilitar (cierra sus sesiones) y restablecer la contraseña. Un admin no puede quitarse el rol ni deshabilitarse, y el ISP no se queda sin admin activo | `GET/POST /v1/users`, `PATCH /v1/users/{id}`, `POST /v1/users/{id}/password`, `PATCH /v1/tenant` |
| `/` | Panel: cuántas OLT, routers y la última conciliación; primeros pasos; estado de la plataforma | `/health`, listas |
| `/olts`, `/olts/nueva` | OLT del ISP con su estado (**Sin consultar**, **Responde** o **Sin respuesta**: lo actualiza cada consulta o escritura), con **Consultar**, **Aprovisionar**, **Editar** y **Eliminar** en cada fila (el borrado pide confirmación y dice si hay que rotar el MikroTik), y alta con su **Marca** (VSOL, HSGQ o BCD; las dos últimas son de solo lectura y no muestran **Aprovisionar** ni **Cambiar clave**) y credenciales cifradas, que nunca se vuelven a mostrar. Con una VSOL nueva se dejan usuario y clave vacíos y el servidor usa los de fábrica (aviso para cambiarlos); la clave de enable la pone el cliente | `GET/POST /v1/olts`, `GET /v1/olts/defaults`, `DELETE /v1/olts/{id}` |
| `/olts/:id/editar` | Corregir lo que Olterra tiene guardado: marca, modelo, firmware, IP, **acceso a la CLI (SSH o Telnet)**, puertos y credenciales (incluida la de enable); las claves se vuelven a cifrar y la bitácora solo anota cuáles cambiaron. No toca la OLT: para eso está **Cambiar clave** | `PATCH /v1/olts/{id}` |
| `/olts/:id/clave` | **Cambiar la clave en la OLT** (acceso, enable o las dos, cada una repetida): Olterra la pone, entra con ella en otra sesión y solo entonces la guarda en la OLT y en su bóveda; se ven los pasos con ✓ y los cambios anteriores. Si no puede entrar con la nueva no guarda nada y dice si la anterior sigue sirviendo | `POST /v1/olts/{id}/password`, `/v1/provision-jobs/{id}`, `/v1/olts/{id}/provision-jobs?kind=olt_password` |
| `/olts/:id` | Detalle: consultas de solo lectura con atajos, resultado interpretado, historial. **Aprovisionar**, **Respaldos**, **Editar**, **Cambiar clave**, **Eliminar** y un enlace a su bitácora | `/v1/olts/{id}/commands`, `/queries`, `/plans`, `/v1/plans/{id}` |
| `/olts/:id/respaldos` | **Respaldos** de la configuración: la lista (automático o a pedido, "Sin cambios" o +agregadas −quitadas), **Respaldar ahora** (se abre solo al terminar), **Cambios** (diferencia con el anterior, en colores) y **Completa**, y **Descargar** como `.txt`. Las claves salen tapadas | `/v1/olts/{id}/backups`, `/v1/backups/{id}`, `/v1/backups/{id}/diff` |
| `/monitoreo` | **Monitoreo**: cifras (en línea, caídas, señal baja, alarmas críticas), cada OLT con su estado y su último sondeo (filtra al tocarla), alarmas abiertas (críticas primero; tocar una lleva a la ONU) y la tabla de clientes con filtros y buscador. Cada ONU muestra el nombre del cliente (el puesto en el panel, o la descripción de la OLT), se puede **Editar** (se guarda en Olterra, no en la OLT) y abre su gráfico de señal de 7 días (cursor con tooltip, flechas del teclado, umbrales −25/−27 rotulados y caídas marcadas) y sus alarmas recientes. **Actualizar ahora** consulta ya; la pantalla se refresca sola cada 30 s | `/v1/monitoring/summary`, `/v1/monitoring/onus`, `/v1/monitoring/onus/{olt}/{pon}/{onu}`, `PUT …/{onu}/name`, `/v1/alarms`, `/v1/olts/{id}/monitoring/poll` |
| `/mapa` | **Mapa FTTH** (MapLibre, teselas de OpenFreeMap): modos **Ver** (tocar una caja, cable, poste u OLT abre su detalle), **Caja**, **Cable** (puntos, deshacer, terminar con doble toque), **Poste**, **Ubicar OLT** y **Factibilidad** (radio de 150 m a 1 km). La caja muestra sus puertos con el cliente, su estado y su señal; **Asignar** busca el cliente entre las ONU del monitoreo. Las cajas se pintan con la leyenda (clientes caídos, sin puertos, con puertos, empalme/ODF). Sin `map:write` solo se ve y se busca factibilidad | `/v1/map`, `/v1/map/config`, `/v1/map/boxes`, `/v1/map/cables`, `/v1/map/poles`, `/v1/map/olts/{id}/location`, `/v1/map/feasibility`, `/v1/monitoring/onus` |
| `/bitacora` | **Bitácora** (solo con el permiso `audit:read`, es decir admin): quién hizo qué, cuándo y desde dónde, con lo que cambió (antes → después); filtros por OLT y por tipo y **Ver más** | `GET /v1/audit`, `GET /v1/olts` |
| `/olts/:id/aprovisionar` | Al abrir busca sola en todos los PON (ONU nuevas) y lee la lista de clientes. **Autorizar** pide solo nombre del cliente (tildes y espacios se arreglan), plan, PPPoE y WiFi (las claves a la vista, sin campo de contraseña, para que el navegador no meta ahí un usuario y clave guardados): el resto lo hace el trabajo de alta y se ven los pasos con ✓. **Últimas altas** guarda el resultado. Cada cliente (buscador por nombre) tiene Internet y WiFi, Reiniciar, Copiar como plan y Desautorizar | `POST /v1/olts/{id}/onus/scan`, `/onus/authorize`, `/onus/configure`, `/onus/reboot`, `/onus/delete`, `/v1/provision-jobs/{id}`, `/v1/olts/{id}/provision-jobs` |
| `/plantillas`, `/plantillas/nueva`, `/plantillas/:id` | **Planes** (plantillas de aprovisionamiento): se copian de un cliente que ya navega ("Copiar como plan" en Aprovisionar abre esta pantalla y lee la ONU sola) o se llenan a mano; formulario para el caso de una VLAN y JSON para lo demás. **Gestión remota de la ONU**: firewall, ping, servicios abiertos desde internet (selección múltiple) y usuario y contraseña de administración de la ONU (y la cuenta normal del cliente, activa o desactivada). Las contraseñas se escriben y no se vuelven a mostrar ("Guardada"); avisa si se abre la web sin poner la de administración | `/v1/provision-templates`, `/queries` |
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
