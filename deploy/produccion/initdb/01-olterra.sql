-- Primer arranque de la base en producción (solo con el volumen vacío): roles, base y PostGIS.
--
-- Es deploy/postgres/init.sql con claves generadas. Llegan como variables de entorno del
-- contenedor (OLTERRA_OWNER_PASSWORD, OLTERRA_APP_PASSWORD) y psql las lee con \getenv: no
-- quedan escritas en ningún archivo ni pasan por la línea de comandos.
--
--   olterra_owner  dueño de las tablas; migraciones, olterra-admin y respaldos. BYPASSRLS.
--   olterra_app    la API y los workers. Sin BYPASSRLS: todo pasa por las políticas.

\getenv owner_password OLTERRA_OWNER_PASSWORD
\getenv app_password OLTERRA_APP_PASSWORD
\if :{?owner_password}
\else
\set owner_password ''
\endif
\if :{?app_password}
\else
\set app_password ''
\endif

SELECT length(:'owner_password') >= 16 AND length(:'app_password') >= 16 AS claves_ok \gset
\if :claves_ok
\else
DO $$ BEGIN RAISE EXCEPTION 'Faltan OLTERRA_OWNER_PASSWORD u OLTERRA_APP_PASSWORD (16 caracteres o más)'; END $$;
\endif

CREATE ROLE olterra_owner LOGIN PASSWORD :'owner_password' BYPASSRLS;
CREATE ROLE olterra_app LOGIN PASSWORD :'app_password' NOBYPASSRLS;
CREATE DATABASE olterra OWNER olterra_owner;

\connect olterra

-- PostGIS necesita superusuario para instalarse; la migración solo verifica que exista.
CREATE EXTENSION IF NOT EXISTS postgis;
