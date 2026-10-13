"""Respaldos de la configuración de la OLT: lista, respaldar ahora, ver y comparar.

La lógica (pedirlos, guardarlos cifrados, el reloj diario) está en ``api/backups.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from olterra.api import backups
from olterra.api.deps import State, Tenant
from olterra.api.routes.olts import executor_key
from olterra.api.schemas import BackupDetail, BackupDiff, BackupOut, DiffLine
from olterra.db.models import Olt, OltBackup
from olterra.executor.plan import Priority

router = APIRouter(tags=["Respaldos"])


async def _get(session: Any, backup_id: UUID) -> OltBackup:
    backup: OltBackup | None = await session.get(OltBackup, backup_id)
    if backup is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Respaldo no encontrado")
    return backup


@router.get("/v1/olts/{olt_id}/backups", response_model=list[BackupOut])
async def list_backups(
    olt_id: UUID, ctx: Tenant, limit: int = Query(30, ge=1, le=200)
) -> list[OltBackup]:
    ctx.require("olt:read")
    async with ctx.session() as session:
        rows = (
            await session.execute(
                select(OltBackup)
                .where(OltBackup.olt_id == olt_id)
                .order_by(OltBackup.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return list(rows)


@router.post(
    "/v1/olts/{olt_id}/backups", response_model=BackupOut, status_code=status.HTTP_202_ACCEPTED
)
async def backup_now(olt_id: UUID, ctx: Tenant, state: State) -> OltBackup:
    """Respalda ya la configuración de la OLT (es una lectura: ``show running-config``)."""
    ctx.require("olt:read")
    executor_key(state)
    state.require_bus()
    async with ctx.session() as session:
        if await session.get(Olt, olt_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "OLT no encontrada")
        pending = (
            await session.execute(
                select(OltBackup.id).where(
                    OltBackup.olt_id == olt_id, OltBackup.status == "pending"
                )
            )
        ).first()
        if pending is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya se está respaldando esta OLT")
    backup_id = await backups.start_backup(
        state,
        tenant_id=ctx.tenant_id,
        olt_id=olt_id,
        actor=ctx.actor,
        trigger="manual",
        priority=Priority.USER,
    )
    async with ctx.session() as session:
        return await _get(session, backup_id)


@router.get("/v1/backups/{backup_id}", response_model=BackupDetail)
async def get_backup(backup_id: UUID, ctx: Tenant, state: State) -> BackupDetail:
    ctx.require("olt:read")
    async with ctx.session() as session:
        backup = await _get(session, backup_id)
        dek = await state.tenant_dek(session, ctx.tenant_id)
        content = await backups.read_content(session, ctx.tenant_id, dek, backup)
        return BackupDetail(**BackupOut.model_validate(backup).model_dump(), content=content)


@router.get("/v1/backups/{backup_id}/diff", response_model=BackupDiff)
async def diff_backup(
    backup_id: UUID, ctx: Tenant, state: State, against: UUID | None = None
) -> BackupDiff:
    """Qué cambió desde ``against`` (por defecto, el respaldo anterior que salió bien)."""
    ctx.require("olt:read")
    async with ctx.session() as session:
        backup = await _get(session, backup_id)
        if backup.status != "ok":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ese respaldo no tiene configuración")
        base: OltBackup | None
        if against is not None:
            base = await _get(session, against)
            if base.olt_id != backup.olt_id or base.status != "ok":
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Se compara con un respaldo de la misma OLT"
                )
        else:
            base = await backups.previous_ok(session, backup)
        dek = await state.tenant_dek(session, ctx.tenant_id)
        new = await backups.read_content(session, ctx.tenant_id, dek, backup)
        old = (
            await backups.read_content(session, ctx.tenant_id, dek, base)
            if base is not None
            else ""
        )
        added, removed = backups.change_counts(old, new)
        return BackupDiff(
            to_id=backup.id,
            from_id=base.id if base is not None else None,
            from_created_at=base.created_at if base is not None else None,
            added=added,
            removed=removed,
            lines=[DiffLine.model_validate(line) for line in backups.diff_lines(old, new)],
        )
