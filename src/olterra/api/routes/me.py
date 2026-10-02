"""Quién llama: el ISP dueño de la llave y lo que la llave puede hacer."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from olterra.api.deps import Tenant
from olterra.api.schemas import MeOut, TenantOut
from olterra.db.models import Tenant as TenantRow

router = APIRouter(prefix="/v1", tags=["Plataforma"])


@router.get("/me", response_model=MeOut)
async def me(ctx: Tenant) -> MeOut:
    async with ctx.session() as session:
        tenant = await session.get(TenantRow, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant no encontrado")
        return MeOut(
            tenant=TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name),
            key_name=ctx.key_name,
            scopes=list(ctx.scopes),
        )
