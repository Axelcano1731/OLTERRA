-- Alta de Olterra en una base COMPARTIDA (la de Supabase de ISPWatch y Converza).
-- Se corre UNA vez, como el administrador de la base (en Supabase, `postgres`):
--
--   psql "<conexión de administrador>" \
--     -v owner_password="<clave larga>" -v app_password="<otra clave larga>" \
--     -f base-compartida.sql
--
-- Qué deja (todo se puede deshacer, ver el final):
--
--   olterra_owner  dueño del esquema `olterra`: migraciones, olterra-admin y respaldos.
--                  BYPASSRLS, como en la base propia; pero no tiene permiso sobre ninguna
--                  tabla de otro sistema, así que no ve nada de ISPWatch ni de Converza.
--   olterra_app    la API. SIN BYPASSRLS: todo pasa por las políticas de cada ISP.
--   olterra        el esquema propio. Nadie más entra: ni PUBLIC, ni los roles que Supabase
--                  expone por su API (anon, authenticated), que nunca reciben permisos aquí.
--
-- Los dos roles traen `search_path = olterra, public`: las tablas de Olterra van a `olterra`
-- y `public` queda solo para que se resuelvan los tipos y las funciones de PostGIS.
--
-- Las conexiones son pocas a propósito: la base tiene un tope total (60 en el plan actual) y
-- ISPWatch y Converza ya usan una parte.

CREATE ROLE olterra_owner LOGIN PASSWORD :'owner_password' BYPASSRLS CONNECTION LIMIT 6;

CREATE ROLE olterra_app LOGIN PASSWORD :'app_password' NOBYPASSRLS CONNECTION LIMIT 12;

-- Para crear el esquema a nombre del dueño, el administrador es miembro un instante.
GRANT olterra_owner TO CURRENT_USER;

CREATE SCHEMA olterra AUTHORIZATION olterra_owner;

REVOKE olterra_owner FROM CURRENT_USER;

REVOKE ALL ON SCHEMA olterra FROM PUBLIC;

ALTER ROLE olterra_owner SET search_path = olterra, public;

ALTER ROLE olterra_app SET search_path = olterra, public;

-- Deshacer (con cuidado: borra TODO lo de Olterra en esta base):
--   GRANT olterra_owner TO CURRENT_USER;
--   DROP SCHEMA olterra CASCADE;
--   DROP ROLE olterra_app;
--   DROP ROLE olterra_owner;
