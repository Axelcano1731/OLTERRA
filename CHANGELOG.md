# Cambios

## [Sin publicar]

### Agregado

- Interfaz web (`web/`, Vue 3 + Vite + Tailwind): panel, alta y consultas de OLT con su
  historial, túnel (scripts que se muestran una sola vez), conciliación desde archivos o con la
  demo. Ver `docs/INTERFAZ.md`.
- API: `GET /v1/me`, `GET /v1/olts/{id}/commands`, `GET /v1/olts/{id}/plans`,
  `GET /v1/reconciliations`, `POST /v1/reconciliations/files` y `POST /v1/reconciliations/demo`.
- Migración `0002`: historial de consultas por OLT y origen de cada conciliación.
- Despliegue de producción (`deploy/produccion/`): Caddy con HTTPS automático y CSP, roles de
  la base con claves generadas, respaldo diario con prueba de restauración y `./olterra.sh`
  para instalar, actualizar y crear ISP. Imágenes publicadas en GHCR. Ver
  `docs/DESPLIEGUE.md`.

- Concentrador: regla que no deja a los ISP del túnel entrar a los servicios del CHR (Winbox,
  SSH, API) por la IP del túnel.
- Túnel SSTP para RouterOS 6 (no tiene WireGuard): script del router con la CA de Olterra,
  servidor SSTP en el concentrador y aislamiento por lista de interfaces para los dos
  transportes. Al rotar, un router puede cambiar de versión y de transporte. Migración `0003`.

- Base de datos compartida: Olterra puede vivir en un esquema propio (`olterra`) de la base de
  Supabase de ISPWatch y Converza, con dos roles que no ven nada de lo demás
  (`deploy/produccion/base-compartida.sql`). El contenedor `db` pasa a ser opcional, el respaldo
  lleva solo ese esquema y `probar-respaldo` restaura en una base desechable. Pool de conexiones
  chico y configurable. Ver `docs/DESPLIEGUE.md`, 3.1.
- Actualización automática desde `main` (`./olterra.sh auto activar`): cada 5 minutos el
  servidor mira `main`, espera al CI en verde y se actualiza solo, sin llaves en GitHub ni
  puertos nuevos. Un commit que falla no se reintenta; las manuales y la automática comparten
  un candado. `./olterra.sh rama <rama>` pasa el servidor a seguir otra rama.
- Eliminar OLT (`DELETE /v1/olts/{id}`): borra la OLT, su credencial cifrada y su historial de
  consultas; la bitácora conserva quién la borró y cómo era. En la lista de OLT, **Editar** y
  **Eliminar** en cada fila (antes Editar solo estaba dentro del detalle).
- Editar OLT (`PATCH /v1/olts/{id}` y su pantalla): modelo, firmware, IP, puertos y
  credenciales, incluida la clave de enable. Las claves se vuelven a cifrar; la bitácora anota
  cuáles cambiaron, nunca su valor.
- Alta de OLT con la clave de fábrica de VSOL: si el cliente deja usuario y clave vacíos, el
  servidor usa `OLTERRA_VSOL_DEFAULT_USERNAME/PASSWORD` (solo en `.env`) y avisa que conviene
  cambiarla; `GET /v1/olts/defaults` dice si está configurada sin revelarla. La clave de enable
  la sigue pidiendo (es la que configura el cliente).
- Driver VSOL V1600G0-B V1.4.8R validado en laboratorio: comandos propios de `onu.optical`,
  `onu.description` y `pon.statistics` (el manual de otros modelos no aplica), nuevos
  `onu.state` y `onu.distance`, captura con `--modelo/--firmware-olt` y captura anonimizada en
  `tests/fixtures/vsol-gpon/V1600G0-B`. Ver `docs/DRIVERS_VSOL.md`.
- Alta de OLT: el modelo se escribe como sale en la web de la OLT (sugerencias con V1600G0-B)
  y el driver lo compara sin guiones (V1600G1-B = V1600G1B).

### Corregido

- La salida de la OLT perdía columnas: la V1600G0-B coloca cada columna con el cursor
  (`` + `ESC[nC`) y el normalizador las pisaba, así que `show onu info`, `state` y
  `rx_power` no se interpretaban. Ahora se emula el cursor.
- `olterra.sh` ya no puede ejecutar basura al final de `actualizar`: el `git pull` que trae una
  versión nueva del propio script cambiaba el archivo mientras bash lo leía.

- El ejecutor y el consumidor de resultados de la API morían a los pocos segundos sin
  trabajo: nats-py a veces lanza el `TimeoutError` de asyncio en vez del suyo y solo se
  atrapaba el suyo.

## [0.1.0] — sin publicar

Esqueleto de la fase 0.

- Conciliación OLT ↔ MikroTik ↔ CRM (`olterra-conciliar`) con 13 tipos de hallazgo, reportes
  HTML/CSV/JSON y fuentes CSV, RouterOS (texto y API), ISPWatch (API de socios) y capturas de OLT.
- Driver VSOL GPON: catálogo de comandos con fuente, parsers, OIDs SNMP y matriz de
  capacidades por modelo y firmware.
- Ejecutor genérico (SSH + SNMP) con colas por OLT, credenciales selladas y NATS JetStream.
- Captura de laboratorio (`olterra-capture`) y simulador de OLT VSOL por SSH.
- Generador del túnel WireGuard + NAT 1:1 para MikroTik y del concentrador.
- API FastAPI sobre PostgreSQL + PostGIS con Row Level Security por tenant y bóveda por sobre.
