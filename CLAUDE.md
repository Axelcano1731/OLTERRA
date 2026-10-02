# Olterra — notas para trabajar en este repo

Gestión de OLT VSOL + mapa FTTH amarrados al CRM del ISP. Plan: `docs/ARQUITECTURA.md`
(el Anexo A tiene las decisiones de implementación). Python 3.12+, FastAPI, PostgreSQL +
PostGIS con RLS, NATS JetStream.

## Comandos

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check src tests migrations; .venv\Scripts\ruff format src tests migrations
.venv\Scripts\mypy
.\scripts\servicios-locales.ps1 iniciar   # PostgreSQL+PostGIS y NATS sin Docker
```

Pruebas de integración: `OLTERRA_TEST_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres`
y `OLTERRA_TEST_NATS_URL=nats://127.0.0.1:4223`. Sin ellas se saltan, no fallan.

## Reglas que no se negocian

- **Aislamiento:** toda tabla con `tenant_id` lleva RLS + FORCE + política, FK compuestas
  `(tenant_id, id)` y una prueba en `tests/integration/test_rls.py`. Ver `docs/BASE_DATOS.md`.
- **Secretos:** nunca en claro en NATS, logs, respuestas ni línea de comandos. Credenciales
  hacia el ejecutor van selladas (`security/sealed.py`); en la base, cifradas por tenant
  (`security/vault.py`). `Credential.model_dump_json()` tapa las claves con asteriscos: para
  sellar o cifrar se usa `reveal_json()`.
- **Drivers:** todo parámetro de usuario pasa por `PARAM_TYPES` (sin espacios ni saltos de
  línea). Un comando solo es `verified=True` con una captura en `tests/fixtures`.
  Los parsers lanzan `UnrecognizedOutput` antes que adivinar.
- **Ejecutor genérico:** no sabe de VSOL; todo lo específico va en el driver (nube).
- **Scripts RouterOS:** validación estricta de todo lo que entra, ASCII, idempotentes (comentario
  `olterra`), sin `on-error` que trague errores.
- **Documentación** en el mismo cambio que el código (tabla en `README.md`).
- **Git:** rama + PR, nunca push a `main`; `git add` con rutas explícitas.

## Idioma

Identificadores en inglés; comentarios, docs y mensajes al usuario en español.
