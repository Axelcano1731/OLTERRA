-- Arranque de la base de Olterra. Corre UNA vez, como superusuario.
-- En docker compose lo ejecuta la imagen de PostGIS al crear el volumen.
--
-- Dos roles, a propósito:
--   olterra_owner  dueño de las tablas; corre migraciones y la administración
--                  (crear tenants). Tiene BYPASSRLS: nunca lo usan la API ni el ejecutor.
--   olterra_app    la API y los workers. SIN BYPASSRLS: todo lo que lee o escribe
--                  pasa por las políticas de cada tenant.
--
-- Las claves de abajo son de DESARROLLO. En producción se crean con claves
-- generadas y se guardan en el gestor de secretos del servidor.

CREATE ROLE olterra_owner LOGIN PASSWORD 'olterra_owner' BYPASSRLS;
CREATE ROLE olterra_app LOGIN PASSWORD 'olterra_app' NOBYPASSRLS;

CREATE DATABASE olterra OWNER olterra_owner;

\connect olterra

-- PostGIS necesita superusuario para instalarse; la migración solo verifica que exista.
CREATE EXTENSION IF NOT EXISTS postgis;
