"""Routers del ISP en el túnel: alta, script para el MikroTik y alta en el concentrador."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.schemas import RouterCreate, RouterOut, RouterScriptOut
from olterra.api.state import AppState, audit
from olterra.db.models import Olt, TunnelRouter
from olterra.db.models import Tenant as TenantRow
from olterra.tunnel.routeros import (
    HubPeer,
    IspTunnel,
    OltMapping,
    render_hub_peer,
    render_isp_script,
)
from olterra.tunnel.wireguard import generate_keypair

router = APIRouter(prefix="/v1/tunnel/routers", tags=["Túnel"])


def _hub(state: AppState) -> tuple[str, str]:
    settings = state.settings
    if not settings.tunnel_hub_host or not settings.tunnel_hub_public_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Concentrador sin configurar (OLTERRA_TUNNEL_HUB_HOST y OLTERRA_TUNNEL_HUB_PUBLIC_KEY)",
        )
    return settings.tunnel_hub_host, settings.tunnel_hub_public_key


def _ip(value: Any) -> IPv4Address:
    return IPv4Address(str(value).split("/")[0])


async def _scripts(
    session: Any, state: AppState, tenant: TenantRow, row: TunnelRouter, private_key: str
) -> RouterScriptOut:
    hub_host, hub_key = _hub(state)
    olts = (
        await session.execute(
            select(Olt).where(
                Olt.router_id == row.id, Olt.nat_ip.is_not(None), Olt.real_ip.is_not(None)
            )
        )
    ).scalars()
    mappings = [OltMapping(o.name, _ip(o.real_ip), _ip(o.nat_ip)) for o in olts]
    settings = state.settings
    isp_script = render_isp_script(
        IspTunnel(
            tenant=tenant.slug,
            router=row.name,
            private_key=private_key,
            address=_ip(row.overlay_ip),
            hub_host=hub_host,
            hub_port=settings.tunnel_hub_port,
            hub_public_key=hub_key,
            platform_prefix=IPv4Network(settings.tunnel_platform_prefix),
            olts=mappings,
            trap_receiver=IPv4Address(settings.tunnel_trap_receiver),
        )
    )
    hub_script = render_hub_peer(
        HubPeer(
            tenant=tenant.slug,
            router=row.name,
            public_key=row.wg_public_key,
            address=_ip(row.overlay_ip),
            nat_ips=[m.nat_ip for m in mappings],
        )
    )
    return RouterScriptOut(
        router=RouterOut.model_validate(_router_dict(row)),
        isp_script=isp_script,
        hub_script=hub_script,
    )


def _router_dict(row: TunnelRouter) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "peer_index": row.peer_index,
        "overlay_ip": str(row.overlay_ip).split("/")[0],
        "wg_public_key": row.wg_public_key,
        "routeros_version": row.routeros_version,
        "created_at": row.created_at,
    }


@router.get("", response_model=list[RouterOut])
async def list_routers(ctx: Tenant) -> list[RouterOut]:
    ctx.require("tunnel:read")
    async with ctx.session() as session:
        rows = (
            await session.execute(select(TunnelRouter).order_by(TunnelRouter.peer_index))
        ).scalars()
        return [RouterOut.model_validate(_router_dict(r)) for r in rows]


@router.post("", response_model=RouterScriptOut, status_code=status.HTTP_201_CREATED)
async def create_router(body: RouterCreate, ctx: Tenant, state: State) -> RouterScriptOut:
    """Da de alta un MikroTik del ISP y devuelve sus scripts. La llave privada no se guarda."""
    ctx.require("tunnel:write")
    _hub(state)
    private_key, public_key = generate_keypair()
    try:
        async with ctx.session() as session:
            tenant = await session.get(TenantRow, ctx.tenant_id)
            if tenant is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant no encontrado")
            used = set((await session.execute(select(TunnelRouter.peer_index))).scalars())
            peer_index = min(set(range(1, len(used) + 2)) - used)
            row = TunnelRouter(
                tenant_id=ctx.tenant_id,
                name=body.name,
                peer_index=peer_index,
                overlay_ip=str(state.addresses.peer_address(tenant.net_index, peer_index)),
                wg_public_key=public_key,
                routeros_version=body.routeros_version,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            await audit(
                session,
                tenant_id=ctx.tenant_id,
                actor=ctx.actor,
                action="tunnel.router.create",
                target_type="tunnel_router",
                target_id=str(row.id),
                after={
                    "name": row.name,
                    "overlay_ip": str(row.overlay_ip),
                    "public_key": public_key,
                },
                source_ip=ctx.client_ip,
            )
            return await _scripts(session, state, tenant, row, private_key)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un router con ese nombre") from exc


@router.post("/{router_id}/script", response_model=RouterScriptOut)
async def rotate_and_render(router_id: UUID, ctx: Tenant, state: State) -> RouterScriptOut:
    """Rota el par de llaves del router y devuelve los scripts con las OLT actuales."""
    ctx.require("tunnel:write")
    _hub(state)
    private_key, public_key = generate_keypair()
    async with ctx.session() as session:
        row = await session.get(TunnelRouter, router_id)
        tenant = await session.get(TenantRow, ctx.tenant_id)
        if row is None or tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Router no encontrado")
        before = {"public_key": row.wg_public_key}
        row.wg_public_key = public_key
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="tunnel.router.rotate_keys",
            target_type="tunnel_router",
            target_id=str(row.id),
            before=before,
            after={"public_key": public_key, "at": datetime.now(UTC).isoformat()},
            source_ip=ctx.client_ip,
        )
        return await _scripts(session, state, tenant, row, private_key)
