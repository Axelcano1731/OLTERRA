# Manual del desarrollador

> Última actualización: 2026-10-01

## Requisitos

- Python 3.12 o superior (probado con 3.13).
- Node 22.12 o superior para la interfaz (`web/`); CI usa Node 24.
- Para las pruebas de integración: PostgreSQL 17 + PostGIS y NATS con JetStream. Con Docker,
  `docker compose`; sin Docker (Windows), `scripts/servicios-locales.ps1`.

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Servicios locales sin Docker (Windows)

```powershell
.\scripts\servicios-locales.ps1 iniciar     # descarga a .bin\ la primera vez
$env:OLTERRA_TEST_ADMIN_URL = "postgresql://postgres@127.0.0.1:55432/postgres"
$env:OLTERRA_TEST_NATS_URL  = "nats://127.0.0.1:4223"
.\scripts\servicios-locales.ps1 detener
```

PostgreSQL queda en 127.0.0.1:55432 (usuario `postgres` sin clave, solo local) y NATS en
127.0.0.1:4223. Los binarios y datos viven en `.bin\` (ignorado por git).

## Pruebas

```powershell
.venv\Scripts\python -m pytest            # todo lo que se pueda con lo disponible
.venv\Scripts\ruff check src tests migrations
.venv\Scripts\ruff format --check src tests migrations
.venv\Scripts\mypy
```

| Grupo | Necesita | Qué cubre |
|---|---|---|
| `tests/unit` | nada | Bóveda, sellado, llaves, enmascarado, parsers, driver, túnel, conciliación, sesión CLI, colas, ejecutor |
| `tests/integration/test_ssh_simulator.py` | nada | SSH real (asyncssh) contra el simulador de VSOL: login, enable, paginación, llave de host, escrituras con parada |
| `tests/integration/test_rls.py`, `test_api.py` | `OLTERRA_TEST_ADMIN_URL` | RLS en PostgreSQL real y la API de punta a punta (crea una base nueva por corrida y la borra) |
| `tests/integration/test_nats_bus.py` | `OLTERRA_TEST_NATS_URL` | JetStream: deduplicación, cola de trabajo, resultados |
| `tests/unit/test_fixtures.py` | capturas en `tests/fixtures` | Que los parsers reconozcan las salidas reales del laboratorio |

Sin las variables, las pruebas que las necesitan se saltan (no fallan). En CI corren todas.

La interfaz tiene las suyas (ver [INTERFAZ.md](INTERFAZ.md)):

```powershell
cd web
npm run lint; npm run format:check; npm run typecheck; npm test; npm run build
```

## Variables de entorno

Ver `.env.example`. Las importantes:

| Variable | Quién | Qué |
|---|---|---|
| `OLTERRA_DATABASE_URL` | API | Con el rol `olterra_app` |
| `OLTERRA_MIGRATIONS_DATABASE_URL` | migraciones, `olterra-admin` | Con el rol `olterra_owner` |
| `OLTERRA_DB_POOL_SIZE`, `OLTERRA_DB_MAX_OVERFLOW` | API | Conexiones por proceso (3 y 2): la base de producción es compartida y tiene un tope |
| `OLTERRA_MASTER_KEY` | API, `olterra-admin` | Llave maestra de la bóveda (`olterra-admin generar-llave-maestra`) |
| `OLTERRA_EXECUTOR_PUBLIC_KEY` | API | Para sellar credenciales |
| `OLTERRA_EXECUTOR_PRIVATE_KEY` | ejecutor | Para abrirlas. Nunca en la API |
| `OLTERRA_NATS_URL` | API, ejecutor | |
| `OLTERRA_TUNNEL_HUB_HOST`, `_PORT`, `_PUBLIC_KEY` | API | Concentrador WireGuard |

Con `OLTERRA_ENV=prod` la API no arranca sin llave maestra ni llave pública del ejecutor.

## Correr todo en local

```powershell
# 1. Base y tenant
$env:OLTERRA_MIGRATIONS_DATABASE_URL = "postgresql+asyncpg://olterra_owner:olterra_owner@127.0.0.1:55432/olterra"
$env:OLTERRA_MASTER_KEY = (.venv\Scripts\olterra-admin generar-llave-maestra)
.venv\Scripts\olterra-admin migrar
.venv\Scripts\olterra-admin crear-tenant --slug isp-demo --nombre "ISP Demo"
.venv\Scripts\olterra-admin crear-llave --tenant isp-demo --nombre local

# 2. API (otra terminal, con las mismas variables más OLTERRA_DATABASE_URL y las del ejecutor)
.venv\Scripts\uvicorn --factory olterra.api.app:create_app --reload

# 3. Ejecutor y una OLT simulada
.venv\Scripts\olterra-executor
.venv\Scripts\python -m olterra.devtools.vsol_sim --puerto 2222

# 4. Interfaz (otra terminal): entra con la llave del paso 1
cd web; npm install; npm run dev     # http://localhost:5173
```

Con el simulador, la OLT se da de alta con IP `127.0.0.1`, puerto SSH `2222`, usuario
`admin` y clave `olterra-sim`. El simulador no conoce todos los comandos: los que no, salen
como "Falló" con su salida, que es justo lo que pasa con un firmware distinto.

La base `olterra` tiene que existir con los roles de `deploy/postgres/init.sql` (con Docker
lo hace la imagen; con el script local, correr esas sentencias una vez como `postgres`).
La documentación interactiva de la API queda en `http://localhost:8000/docs`.

Con Docker: ver el encabezado de `docker-compose.yml` (desarrollo). Producción tiene su propio
stack en `deploy/produccion/`: ver [DESPLIEGUE.md](DESPLIEGUE.md).

## Herramientas de línea de comandos

| Comando | Para qué |
|---|---|
| `olterra-conciliar demo` | Reporte de conciliación con datos sintéticos (demo para pilotos) |
| `olterra-conciliar plantillas` | CSV vacíos con las columnas que acepta |
| `olterra-conciliar correr ...` | Conciliación con datos reales: CSV, export de RouterOS, API de RouterOS, API de socios de ISPWatch, capturas de OLT |
| `olterra-capture ...` | Captura de laboratorio (ver LABORATORIO.md) |
| `olterra-admin ...` | Llaves, migraciones, tenants, script del concentrador |
| `olterra-executor` | El ejecutor |
| `python -m olterra.devtools.vsol_sim` | OLT VSOL simulada por SSH (salidas sintéticas o capturas reales) |

## Mapa del código

```
src/olterra/
  config.py            variables OLTERRA_*
  identifiers.py       seriales GPON, MAC y llaves laxas para detectar errores de digitación
  security/            bóveda por sobre, credenciales selladas, llaves de API, enmascarado
  executor/            contrato de planes, sesión CLI, SSH, SNMP, colas por OLT, bus NATS, servicio
  drivers/             base genérica + vsol_gpon (comandos, capacidades, parsers, SNMP)
  orchestrator.py      lado nube: arma planes sellados e interpreta resultados
  tunnel/              plan de direcciones, llaves WireGuard, scripts RouterOS
  reconciliation/      motor, fuentes, reportes, CLI
  capture/             olterra-capture
  db/                  modelos y sesiones con tenant
  api/                 FastAPI
  devtools/            simulador de OLT y datos de demo
migrations/            Alembic (el esquema manda aquí)
web/                   interfaz (Vue 3 + Vite + Tailwind): api/, lib/, components/, views/
```

## Convenciones

- Código en inglés; comentarios, documentación y mensajes al usuario en español.
- La documentación se actualiza en el **mismo** cambio que el código (tabla de qué tocar en
  el README).
- Ramas de trabajo y PR; nunca push directo a `main`. `git add` con rutas explícitas.
- Nada de claves en la línea de comandos ni en logs: variables de entorno, bóveda y `redact()`.
- Un comando del driver solo se marca `verified=True` con una captura que lo respalde.
