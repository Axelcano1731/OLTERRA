"""Bitácora: quién hizo qué, cuándo, desde dónde, con el antes y el después.

La escribe la API en cada cambio (``api/state.audit``) y para la aplicación es solo de anexar:
aquí solo se lee. Nunca lleva claves: de las credenciales anota cuáles cambiaron, no su valor.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from olterra.api.deps import Tenant
from olterra.api.schemas import AuditOut
from olterra.db.models import AuditLog

router = APIRouter(tags=["Bitácora"])


@router.get("/v1/audit", response_model=list[AuditOut])
async def list_audit(
    ctx: Tenant,
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None, description="Para seguir: el id más viejo de la página"),
    target_id: str | None = Query(None, max_length=64, description="Por ejemplo, una OLT"),
    action: str | None = Query(
        None, max_length=64, pattern=r"^[a-z_.]+$", description="Prefijo: 'olt.' u 'onu.'"
    ),
) -> list[AuditLog]:
    ctx.require("audit:read")
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before is not None:
        query = query.where(AuditLog.id < before)
    if target_id is not None:
        query = query.where(AuditLog.target_id == target_id)
    if action is not None:
        query = query.where(AuditLog.action.startswith(action, autoescape=True))
    async with ctx.session() as session:
        return list((await session.execute(query)).scalars())
