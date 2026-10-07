# Base de datos

> Última actualización: 2026-10-03 · Migración vigente: `0003`

PostgreSQL 17 + PostGIS. El esquema manda en `migrations/versions/`; `src/olterra/db/models.py`
lo refleja para el ORM.

## Roles

| Rol | Lo usan | Puede |
|---|---|---|
| `olterra_owner` | Migraciones (`alembic`, `olterra-admin migrar`) y `olterra-admin` | Todo. Tiene BYPASSRLS: nunca lo usan la API ni el ejecutor |
| `olterra_app` | API y workers | Solo lo que le da la migración, y siempre bajo RLS |

Los crea `deploy/postgres/init.sql` (una vez, como superusuario), junto con la base y la
extensión PostGIS. En producción, con claves generadas.

**Base compartida (Supabase).** En producción las tablas viven en el esquema `olterra` de la base
de ISPWatch y Converza, y los roles los crea `deploy/produccion/base-compartida.sql`: mismos
dos roles, con `search_path = olterra, public` y un tope de conexiones. `olterra_owner` sigue
con `BYPASSRLS`, pero no tiene permiso sobre ninguna tabla de otro sistema; `olterra_app` tampoco
(lo prueba `tests/integration/test_shared_database.py`). La migración `0001` da `USAGE` al
esquema donde corre (`current_schema()`), no a `public`. Ver DESPLIEGUE.md, 3.1.

## Aislamiento por tenant

- La función `olterra_current_tenant()` lee `olterra.tenant_id`. La aplicación lo fija por
  transacción con `tenant_session()` (`set_config(..., true)`). Sin él, la función da NULL y
  ninguna política deja ver ni escribir nada.
- Toda tabla con `tenant_id` tiene `ENABLE` y `FORCE ROW LEVEL SECURITY` y la política
  `tenant_isolation` (`USING` y `WITH CHECK` contra el tenant actual). `tenants` compara su `id`.
- Las FK entre tablas de tenant son **compuestas** `(tenant_id, x_id)`: las FK no respetan
  RLS, y con una FK simple una fila podía apuntar a la de otro tenant.
- Los índices únicos valen entre tenants aunque la aplicación no vea las filas ajenas (por
  eso `nat_ip` y `overlay_ip` no chocan nunca).

## Tablas

| Tabla | Para qué | La aplicación puede |
|---|---|---|
| `tenants` | Un ISP. `net_index` (secuencia) define sus bloques de direcciones del túnel | leer |
| `tenant_keys` | La DEK del tenant, envuelta por la llave maestra | leer |
| `api_keys` | Llaves de API (solo el SHA-256 del secreto) | leer; actualizar `last_used_at` |
| `credentials` | Credenciales cifradas con la DEK (AAD = tenant + `credential:<id>`) | leer, crear, cambiar, borrar |
| `tunnel_routers` | MikroTik del ISP en el túnel: IP del overlay y su transporte, con la llave pública WireGuard (RouterOS 7) o el usuario SSTP (RouterOS 6). Las claves privadas no se guardan | leer, crear, cambiar, borrar |
| `olts` | OLT: driver, modelo, firmware, IP real, IP NAT única, credencial, llave SSH fijada, capacidades, ubicación (PostGIS) | leer, crear, cambiar, borrar |
| `onus` | ONU por OLT/PON/índice: serial, fase, potencias, distancia, cliente | leer, crear, cambiar, borrar |
| `plan_runs` | Planes enviados al ejecutor y su resultado ya interpretado (salidas enmascaradas). Índice por OLT para el historial de consultas | leer, crear, cambiar, borrar |
| `provision_templates` | Plantillas de aprovisionamiento de ONU (`body` = perfiles, T-CONT, GEM, VLAN, WAN PPPoE, WiFi). Nada de un cliente ni claves | leer, crear, cambiar, borrar |
| `users` | Usuarios de la interfaz: usuario único en la plataforma (sin distinguir mayúsculas), hash scrypt, rol, cambio obligatorio de contraseña, intentos fallidos y bloqueo | leer y cambiar (los crea `olterra-admin`) |
| `user_sessions` | Sesiones de usuario: solo el SHA-256 del secreto del token, vencimiento y cierre | leer, crear, cambiar, borrar |
| `provision_jobs` | Trabajos de alta de ONU: paso actual, datos del cliente sin claves, detalle y bitácora de pasos. Las claves PPPoE/WiFi van cifradas con la DEK del tenant (`secrets`) y se borran al terminar | leer, crear, cambiar, borrar |
| `audit_log` | Bitácora: quién, qué, cuándo, desde dónde, antes y después | **solo leer y anexar** |
| `reconciliation_runs` | Corridas de conciliación con sus hallazgos, su origen (`api`, `upload` o `demo`) y, si vienen de archivos, nombre, tipo y registros de cada uno (el contenido no se guarda) | leer, crear, cambiar, borrar |

## Funciones que no esperan al tenant

Hay dos momentos en que todavía no se sabe el tenant, y para cada uno hay una función
`SECURITY DEFINER` (del rol dueño) que **solo devuelve identificadores**; todo lo demás se lee
después con el tenant fijado y bajo RLS. Solo `olterra_app` las puede ejecutar.

| Función | Para qué | Devuelve |
|---|---|---|
| `olterra_login_lookup(usuario)` | Entrar: el usuario escribe su nombre, no su ISP | `(tenant_id, user_id)` si existe, está activo y su ISP también |
| `olterra_due_provision_jobs()` | El reloj de la API que avanza los trabajos de alta | `(tenant_id, job_id)` de los que les toca o se quedaron sin respuesta |

## Para sumar una tabla de tenant

1. Columna `tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE`.
2. Si la referencian otras tablas de tenant: `UNIQUE (tenant_id, id)`; las FK hacia ella,
   compuestas.
3. Agregarla a `TENANT_TABLES` en la migración (activa RLS, FORCE y la política) y darle a
   `olterra_app` solo los permisos que necesita.
4. Una prueba en `tests/integration/test_rls.py` que intente leerla y escribirla desde otro tenant.
5. Actualizar esta página.

## Migraciones

| Migración | Qué hace |
|---|---|
| `0001` | Esquema base con RLS en todas las tablas de tenant |
| `0002` | Lo que pide la interfaz: índice de `plan_runs` por OLT y columnas `source` y `files` en `reconciliation_runs` |
| `0003` | Túnel SSTP para RouterOS 6: `transport` y `ppp_user` en `tunnel_routers`; cada transporte exige su credencial |
| `0004` | `provision_templates` con RLS + FORCE y su política |
| `0005` | `users`, `user_sessions`, `provision_jobs` (RLS + FORCE), `olts.pon_ports` y las funciones `olterra_login_lookup` y `olterra_due_provision_jobs` |

## Correr las migraciones

```powershell
$env:OLTERRA_MIGRATIONS_DATABASE_URL = "postgresql+asyncpg://olterra_owner:...@host:5432/olterra"
.venv\Scripts\olterra-admin migrar        # o: alembic upgrade head
```

Las migraciones no corren al arrancar la API (una migración lenta tumbaría el despliegue por
health checks, como ya le pasó a ISPWatch): se corren antes, como paso explícito.
