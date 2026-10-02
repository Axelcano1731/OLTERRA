# Cambios

## [Sin publicar]

### Agregado

- Interfaz web (`web/`, Vue 3 + Vite + Tailwind): panel, alta y consultas de OLT con su
  historial, túnel (scripts que se muestran una sola vez), conciliación desde archivos o con la
  demo. Ver `docs/INTERFAZ.md`.
- API: `GET /v1/me`, `GET /v1/olts/{id}/commands`, `GET /v1/olts/{id}/plans`,
  `GET /v1/reconciliations`, `POST /v1/reconciliations/files` y `POST /v1/reconciliations/demo`.
- Migración `0002`: historial de consultas por OLT y origen de cada conciliación.

### Corregido

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
