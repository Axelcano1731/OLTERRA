"""Estado y resultado de los planes enviados al ejecutor."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from olterra.api.deps import Tenant
from olterra.api.schemas import PlanOut
from olterra.db.models import PlanRun

router = APIRouter(prefix="/v1/plans", tags=["Planes"])


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: UUID, ctx: Tenant) -> PlanOut:
    ctx.require("olt:read")
    async with ctx.session() as session:
        run = await session.get(PlanRun, plan_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan no encontrado")
        return PlanOut(
            plan_id=run.id,
            olt_id=run.olt_id,
            status=run.status,
            created_at=run.created_at,
            finished_at=run.finished_at,
            result=run.result,
        )
