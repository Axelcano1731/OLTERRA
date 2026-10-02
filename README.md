# Olterra

**Tu OLT, tu fibra y tus clientes en un solo mapa.**

Gestión de OLT VSOL y mapa FTTH en un solo modelo de datos, amarrados al CRM del ISP
(ISPWatch, WispHub, Mikrowisp). Tercera pieza del ecosistema ISPWatch + Converza.

Estado: **fase 0** (esqueleto técnico, laboratorio y pilotos). Plan completo en
[docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Qué hay hoy

| Pieza | Estado |
|---|---|
| Conciliación OLT ↔ MikroTik ↔ CRM (`olterra-conciliar`) | Lista para demo con pilotos: 13 tipos de descuadre con acción sugerida; lee CSV/Excel, export de RouterOS, API de RouterOS, API de socios de ISPWatch y capturas de OLT |
| Captura de laboratorio (`olterra-capture`) | Lista: corre el catálogo de lectura y deja salidas listas para volverse pruebas |
| Driver VSOL GPON | Catálogo de 51 comandos con su fuente, parsers tolerantes, OIDs del MIB, matriz de capacidades por modelo. **Sin verificar en laboratorio** |
| Ejecutor genérico + NATS JetStream | Listo: colas por OLT con prioridad, una sesión por OLT, credenciales selladas, deduplicación |
| Túnel WireGuard + NAT 1:1 para MikroTik | Generador listo y probado; falta correrlo contra el CHR real |
| API FastAPI + PostgreSQL/PostGIS con RLS | Esqueleto: OLT, consultas de lectura con su catálogo e historial, túnel, conciliación (JSON, archivos o demo), planes |
| Despliegue (`deploy/produccion/`) | Listo para un droplet de DigitalOcean: Caddy con HTTPS, imágenes en GHCR, respaldos diarios que se prueban restaurando; todo con `./olterra.sh`. CI lo instala de punta a punta en cada PR |
| Interfaz web (`web/`) | Lista para pilotos: panel, alta y consultas de OLT, túnel, conciliación desde archivos o con la demo. Entra con la llave de API (aún sin usuarios) |
| Mapa FTTH, autorización de ONU, monitoreo, app de campo | Fases 1 a 3 |

## Probarlo en un minuto

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\olterra-conciliar demo
```

Abre el HTML que queda en `reportes/`: es la demo de conciliación con datos sintéticos.

La interfaz web necesita la API corriendo (ver el manual del desarrollador) y Node 22.12+:

```powershell
cd web; npm install; npm run dev      # http://localhost:5173
```

## Documentación

| Documento | Qué tiene | Se actualiza cuando cambia… |
|---|---|---|
| [ARQUITECTURA.md](docs/ARQUITECTURA.md) | El plan y las decisiones de implementación (Anexo A) | Diseño, stack, integraciones, despliegue |
| [LABORATORIO.md](docs/LABORATORIO.md) | Protocolo de la fase 0: preparar la OLT, capturar, revisar, preguntas abiertas | Lo que se prueba en el laboratorio |
| [DRIVERS_VSOL.md](docs/DRIVERS_VSOL.md) | Comandos, SNMP, capacidades por modelo y sus fuentes | Un comando, parser u OID |
| [BASE_DATOS.md](docs/BASE_DATOS.md) | Roles, RLS, tablas, cómo sumar una tabla | Una migración |
| [DESPLIEGUE.md](docs/DESPLIEGUE.md) | Producción: servidor, dominio, `./olterra.sh`, respaldos, túnel y seguridad | `deploy/produccion/`, imágenes, variables de producción |
| [INTERFAZ.md](docs/INTERFAZ.md) | Pantallas, cómo habla con la API, sesión, seguridad y comandos de la interfaz | Una pantalla, un endpoint que usa, la sesión |
| [MANUAL_DESARROLLADOR.md](docs/MANUAL_DESARROLLADOR.md) | Instalación, pruebas, variables, herramientas | Entorno, comandos, pruebas |
| [CHANGELOG.md](CHANGELOG.md) | Versiones | Cada versión |

La documentación se actualiza en el mismo cambio que el código.
