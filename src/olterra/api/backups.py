"""Respaldos de la configuración de las OLT: diarios y a pedido, con diferencias entre versiones.

Un respaldo es un plan de solo lectura (``show running-config``) con prioridad baja: va después de
lo que pida un usuario. El ejecutor tapa las claves antes de devolver la salida (nunca viajan en
claro por NATS), así que el respaldo sirve para ver qué cambió y cuándo, no para restaurar las
claves. Se guarda comprimido y cifrado con la llave del ISP; su huella (``sha256``) dice si cambió
sin descifrar el anterior.

El reloj (``tick``) respalda cada OLT que ya respondió alguna vez y no tiene un respaldo en
``OLTERRA_BACKUP_EVERY_HOURS``. Un respaldo que no se pudo hacer también cuenta: no se reintenta
cada cinco minutos contra una OLT caída.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from olterra.api.routes.olts import executor_key, olt_access, publish_plan, record_plan
from olterra.api.state import AppState
from olterra.db.models import OltBackup, PlanRun
from olterra.db.session import tenant_session
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.executor.plan import Plan, PlanResult, Priority
from olterra.orchestrator import build_read_plan
from olterra.security.vault import Vault

log = logging.getLogger(__name__)

COMMAND = "system.running_config"
AUTO_ACTOR = "olterra:respaldo"
TIMEOUT_S = 180.0  # una running-config con cientos de ONU tarda


def _aad(backup_id: UUID) -> str:
    return f"olt_backup:{backup_id}"


def seal_content(tenant_id: UUID, dek: bytes, backup_id: UUID, content: str) -> bytes:
    return Vault.encrypt(tenant_id, dek, _aad(backup_id), zlib.compress(content.encode("utf-8"), 9))


def open_content(tenant_id: UUID, dek: bytes, backup: OltBackup) -> str:
    if backup.content is None:
        return ""
    plain = Vault.decrypt(tenant_id, dek, _aad(backup.id), backup.content)
    return zlib.decompress(plain).decode("utf-8")


def normalize(output: str) -> str:
    """El texto que se guarda y se compara: sin espacios al final ni líneas vacías en los bordes."""
    lines = [line.rstrip() for line in output.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip("\n") + "\n"


def change_counts(old: str, new: str) -> tuple[int, int]:
    """Líneas agregadas y quitadas de ``old`` a ``new``."""
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def diff_lines(old: str, new: str, context: int = 3) -> list[dict[str, str]]:
    """Diferencia por líneas para la interfaz: ``kind`` es ``+``, ``-``, `` `` o ``@`` (salto)."""
    lines: list[dict[str, str]] = []
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=context)
    for raw in diff:
        if raw.startswith(("---", "+++")):
            continue
        if raw.startswith("@@"):
            lines.append({"kind": "@", "text": raw})
        else:
            lines.append({"kind": raw[:1] or " ", "text": raw[1:]})
    return lines


async def start_backup(
    state: AppState,
    *,
    tenant_id: UUID,
    olt_id: UUID,
    actor: str,
    trigger: str,
    priority: Priority,
) -> UUID:
    """Crea el respaldo y encola su plan. Si no se puede ni pedir, queda como fallido."""
    backup_id = uuid4()
    plan: Plan | None = None
    async with tenant_session(state.sessions, tenant_id) as session:
        backup = OltBackup(
            id=backup_id,
            tenant_id=tenant_id,
            olt_id=olt_id,
            trigger=trigger,
            requested_by=actor,
        )
        session.add(backup)
        try:
            olt, credential, target = await olt_access(session, tenant_id, state, olt_id)
            calls = [CommandCall(COMMAND)]
            plan = build_read_plan(
                get_driver(olt.driver),
                tenant_id=tenant_id,
                olt_id=olt.id,
                target=target,
                calls=calls,
                credential=credential,
                executor_public_key=executor_key(state),
                model=olt.model,
                firmware=olt.firmware,
                priority=priority,
                timeout_s=TIMEOUT_S,
            )
            await record_plan(session, tenant_id, actor, olt, plan, calls)
            backup.plan_id = plan.plan_id
        except HTTPException as exc:
            _fail(backup, str(exc.detail))
            plan = None
        except Exception:
            # Queda registrado como fallido: si no, el reloj insistiría cada cinco minutos.
            log.exception("No se pudo preparar el respaldo de la OLT %s", olt_id)
            _fail(backup, "No se pudo preparar el respaldo (revisa la credencial de la OLT)")
            plan = None
    if plan is not None:
        try:
            await publish_plan(state, plan)
        except HTTPException as exc:
            async with tenant_session(state.sessions, tenant_id) as session:
                failed = await session.get(OltBackup, backup_id)
                if failed is not None:
                    _fail(failed, str(exc.detail))
    return backup_id


def _fail(backup: OltBackup, error: str) -> None:
    backup.status = "failed"
    backup.error = error
    backup.finished_at = datetime.now(UTC)


async def previous_ok(session: AsyncSession, backup: OltBackup) -> OltBackup | None:
    """El respaldo anterior que salió bien (con el que se compara)."""
    return (
        await session.execute(
            select(OltBackup)
            .where(
                OltBackup.olt_id == backup.olt_id,
                OltBackup.status == "ok",
                OltBackup.id != backup.id,
                OltBackup.created_at <= backup.created_at,
            )
            .order_by(OltBackup.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def read_content(
    session: AsyncSession, tenant_id: UUID, dek: bytes, backup: OltBackup
) -> str:
    """La configuración del respaldo; si no cambió, la del último que sí la guardó."""
    holder: OltBackup | None = backup
    if backup.content is None and backup.sha256 is not None:
        holder = (
            await session.execute(
                select(OltBackup)
                .where(
                    OltBackup.olt_id == backup.olt_id,
                    OltBackup.sha256 == backup.sha256,
                    OltBackup.content.is_not(None),
                    OltBackup.created_at <= backup.created_at,
                )
                .order_by(OltBackup.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return open_content(tenant_id, dek, holder) if holder is not None else ""


async def on_result(state: AppState, result: PlanResult) -> None:
    """Si el plan era un respaldo, guarda la configuración (cifrada) y cuánto cambió."""
    async with tenant_session(state.sessions, result.tenant_id) as session:
        backup = (
            await session.execute(
                select(OltBackup).where(
                    OltBackup.plan_id == result.plan_id, OltBackup.status == "pending"
                )
            )
        ).scalar_one_or_none()
        if backup is None:
            return
        run = await session.get(PlanRun, result.plan_id)
        outputs: list[dict[str, Any]] = list(
            ((run.result if run else None) or {}).get("outputs", [])
        )
        entry = next((o for o in outputs if o.get("key") == COMMAND), None)
        if entry is None or not entry.get("ok") or entry.get("output") is None:
            error = (entry or {}).get("error") or ((run.result or {}).get("error") if run else None)
            _fail(backup, str(error or "La OLT no devolvió su configuración"))
            return
        content = normalize(str(entry["output"]))
        dek = await state.tenant_dek(session, result.tenant_id)
        previous = await previous_ok(session, backup)
        backup.sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        backup.size_bytes = len(content.encode("utf-8"))
        backup.line_count = content.count("\n")
        if previous is None:
            backup.changed, backup.added, backup.removed = True, backup.line_count, 0
        elif previous.sha256 == backup.sha256:
            backup.changed, backup.added, backup.removed = False, 0, 0
        else:
            old = await read_content(session, result.tenant_id, dek, previous)
            backup.added, backup.removed = change_counts(old, content)
            backup.changed = True
        # Sin cambios no se guarda otra copia: la tiene el último respaldo con la misma huella.
        if backup.changed:
            backup.content = seal_content(result.tenant_id, dek, backup.id, content)
        backup.status = "ok"
        backup.finished_at = datetime.now(UTC)
        if run is not None and run.result is not None:
            # La configuración queda solo en el respaldo (cifrada), no repetida en el historial.
            run.result = {
                **run.result,
                "outputs": [
                    {**o, "output": None, "backup_id": str(backup.id)}
                    if o.get("key") == COMMAND
                    else o
                    for o in outputs
                ],
            }


async def tick(state: AppState) -> int:
    """Pide los respaldos que tocan. Devuelve cuántos pidió."""
    hours = state.settings.backup_every_hours
    if hours <= 0 or state.bus is None or not state.settings.executor_public_key:
        return 0
    async with state.sessions() as session:
        due = (
            await session.execute(
                text("SELECT tenant_id, olt_id FROM olterra_due_backups(:every)"),
                {"every": timedelta(hours=hours)},
            )
        ).all()
    for tenant_id, olt_id in due:
        try:
            await start_backup(
                state,
                tenant_id=tenant_id,
                olt_id=olt_id,
                actor=AUTO_ACTOR,
                trigger="auto",
                priority=Priority.POLL,
            )
        except Exception:
            log.exception("No se pudo pedir el respaldo de la OLT %s", olt_id)
    return len(due)


async def run_scheduler(state: AppState, every: float = 300.0) -> None:
    while True:
        try:
            await tick(state)
        except Exception:
            log.exception("Reloj de respaldos")
        await asyncio.sleep(every)
