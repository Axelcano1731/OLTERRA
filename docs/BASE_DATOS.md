# Base de datos

> Última actualización: 2026-10-01 · Migración vigente: `0001`

PostgreSQL 17 + PostGIS. El esquema manda en `migrations/versions/`; `src/olterra/db/models.py`
lo refleja para el ORM.

## Roles

| Rol | Lo usan | Puede |
|---|---|---|
| `olterra_owner` | Migraciones (`alembic`, `olterra-admin migrar`) y `olterra-admin` | Todo. Tiene BYPASSRLS: nunca lo usan la API ni el ejecutor |
| `olterra_app` | API y workers | Solo lo que le da la migración, y siempre bajo RLS |

Los crea `deploy/postgres/init.sql` (una vez, como superusuario), junto con la base y la
extensión PostGIS. En producción, con claves generadas.

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
| `tunnel_routers` | MikroTik del ISP en el túnel: IP del overlay y llave pública WireGuard | leer, crear, cambiar, borrar |
| `olts` | OLT: driver, modelo, firmware, IP real, IP NAT única, credencial, llave SSH fijada, capacidades, ubicación (PostGIS) | leer, crear, cambiar, borrar |
| `onus` | ONU por OLT/PON/índice: serial, fase, potencias, distancia, cliente | leer, crear, cambiar, borrar |
| `plan_runs` | Planes enviados al ejecutor y su resultado ya interpretado (salidas enmascaradas) | leer, crear, cambiar, borrar |
| `audit_log` | Bitácora: quién, qué, cuándo, desde dónde, antes y después | **solo leer y anexar** |
| `reconciliation_runs` | Corridas de conciliación con sus hallazgos | leer, crear, cambiar, borrar |

## Para sumar una tabla de tenant

1. Columna `tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE`.
2. Si la referencian otras tablas de tenant: `UNIQUE (tenant_id, id)`; las FK hacia ella,
   compuestas.
3. Agregarla a `TENANT_TABLES` en la migración (activa RLS, FORCE y la política) y darle a
   `olterra_app` solo los permisos que necesita.
4. Una prueba en `tests/integration/test_rls.py` que intente leerla y escribirla desde otro tenant.
5. Actualizar esta página.

## Correr las migraciones

```powershell
$env:OLTERRA_MIGRATIONS_DATABASE_URL = "postgresql+asyncpg://olterra_owner:...@host:5432/olterra"
.venv\Scripts\olterra-admin migrar        # o: alembic upgrade head
```

Las migraciones no corren al arrancar la API (una migración lenta tumbaría el despliegue por
health checks, como ya le pasó a ISPWatch): se corren antes, como paso explícito.
