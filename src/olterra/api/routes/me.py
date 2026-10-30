"""Quién llama (el ISP y lo que puede hacer) y el nombre del ISP, que cambia su admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from olterra.api.deps import Tenant
from olterra.api.schemas import MeOut, TenantOut, UserOut
from olterra.api.state import audit
from olterra.db.models import Tenant as TenantRow
from olterra.db.models import User

router = APIRouter(prefix="/v1", tags=["Plataforma"])


@router.get("/me", response_model=MeOut)
async def me(ctx: Tenant) -> MeOut:
    async with ctx.session() as session:
        tenant = await session.get(TenantRow, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant no encontrado")
        user = await session.get(User, ctx.user_id) if ctx.user_id else None
        return MeOut(
            tenant=TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name),
            key_name=ctx.key_name,
            scopes=list(ctx.scopes),
            user=UserOut(
                username=user.username,
                display_name=user.display_name,
                role=user.role,  # type: ignore[arg-type]
                must_change_password=user.must_change_password,
            )
            if user
            else None,
        )


class TenantUpdate(BaseModel):
    """El nombre que se muestra del ISP. El identificador (slug) no cambia: va en los scripts de
    los MikroTik y en el concentrador."""

    name: str = Field(min_length=2, max_length=64)


@router.patch("/tenant", response_model=TenantOut)
async def rename_tenant(body: TenantUpdate, ctx: Tenant) -> TenantOut:
    ctx.require("tenant:write")
    name = " ".join(body.name.split())
    if len(name) < 2 or any(not ch.isprintable() for ch in name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nombre del ISP inválido")
    async with ctx.session() as session:
        tenant = await session.get(TenantRow, ctx.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant no encontrado")
        before = {"name": tenant.name}
        tenant.name = name
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="tenant.rename",
            target_type="tenant",
            target_id=str(tenant.id),
            before=before,
            after={"name": tenant.name},
            source_ip=ctx.client_ip,
        )
        return TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name)
