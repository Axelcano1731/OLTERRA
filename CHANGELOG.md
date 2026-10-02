# Cambios

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
