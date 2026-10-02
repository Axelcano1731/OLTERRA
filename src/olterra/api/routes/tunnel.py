"""Routers del ISP en el túnel: alta, script para el MikroTik y alta en el concentrador.

Dos transportes hacia el mismo concentrador: WireGuard (RouterOS 7) y SSTP (RouterOS 6, que
no tiene WireGuard). La credencial del router (llave privada o clave SSTP) se genera aquí,
va una sola vez en los scripts y no se guarda: si hace falta el script otra vez, se rota.
"""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from olterra.api.deps import State, Tenant
from olterra.api.schemas import RouterCreate, RouterOut, RouterScriptOut, RouterUpdate, Transport
from olterra.api.state import AppState, audit
from olterra.db.models import Olt, TunnelRouter
from olterra.db.models import Tenant as TenantRow
from olterra.tunnel.routeros import (
    HubPeer,
    HubSstpPeer,
    IspTunnel,
    OltMapping,
    SstpTunnel,
    render_hub_peer,
    render_hub_sstp_peer,
    render_isp_script,
    render_isp_sstp_script,
)
from olterra.tunnel.sstp import InvalidSstp, generate_password, normalize_ca, ppp_user
from olterra.tunnel.wireguard import generate_keypair

router = APIRouter(prefix="/v1/tunnel/routers", tags=["Túnel"])


def transport_for(version: str | None, explicit: Transport | None = None) -> Transport:
    """RouterOS 6 no tiene WireGuard: va por SSTP. Sin versión se asume 7."""
    if explicit is not None:
        return explicit
    return "sstp" if (version or "").split(".")[0] == "6" else "wireguard"


def _unavailable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)


def _hub(state: AppState, transport: Transport) -> tuple[str, str]:
    """``(host, llave pública o CA)`` del concentrador para ese transporte; 503 si falta."""
    settings = state.settings
    if transport == "wireguard":
        if not settings.tunnel_hub_host or not settings.tunnel_hub_public_key:
            raise _unavailable(
                "Concentrador sin configurar (OLTERRA_TUNNEL_HUB_HOST y OLTERRA_TUNNEL_HUB_PUBLIC_KEY)"
            )
        return settings.tunnel_hub_host, settings.tunnel_hub_public_key
    if not settings.tunnel_hub_host or not settings.tunnel_sstp_ca:
        raise _unavailable(
            "El concentrador no tiene SSTP para RouterOS v6 (OLTERRA_TUNNEL_HUB_HOST y"
            " OLTERRA_TUNNEL_SSTP_CA)"
        )
    try:
        return settings.tunnel_hub_host, normalize_ca(settings.tunnel_sstp_ca)
    except InvalidSstp as exc:
        raise _unavailable(f"OLTERRA_TUNNEL_SSTP_CA: {exc}") from exc


def _ip(value: Any) -> IPv4Address:
    return IPv4Address(str(value).split("/")[0])


def _credentials(row: TunnelRouter, tenant: TenantRow, transport: Transport) -> str:
    """Deja en la fila la credencial nueva del transporte; devuelve el secreto para los scripts."""
    row.transport = transport
    if transport == "sstp":
        row.wg_public_key = None
        row.ppp_user = ppp_user(tenant.slug, row.name)
        return generate_password()
    private_key, public_key = generate_keypair()
    row.wg_public_key = public_key
    row.ppp_user = None
    return private_key


def _identity(row: TunnelRouter) -> dict[str, Any]:
    return {"transport": row.transport, "public_key": row.wg_public_key, "ppp_user": row.ppp_user}


async def _scripts(
    session: Any, state: AppState, tenant: TenantRow, row: TunnelRouter, secret: str
) -> RouterScriptOut:
    hub_host, hub_credential = _hub(state, row.transport)  # type: ignore[arg-type]
    olts = (
        await session.execute(
            select(Olt).where(
                Olt.router_id == row.id, Olt.nat_ip.is_not(None), Olt.real_ip.is_not(None)
            )
        )
    ).scalars()
    mappings = [OltMapping(o.name, _ip(o.real_ip), _ip(o.nat_ip)) for o in olts]
    nat_ips = [m.nat_ip for m in mappings]
    settings = state.settings
    prefix = IPv4Network(settings.tunnel_platform_prefix)
    traps = IPv4Address(settings.tunnel_trap_receiver)
    if row.transport == "sstp":
        user = row.ppp_user or ""
        isp_script = render_isp_sstp_script(
            SstpTunnel(
                tenant=tenant.slug,
                router=row.name,
                user=user,
                password=secret,
                hub_host=hub_host,
                hub_port=settings.tunnel_sstp_port,
                ca_pem=hub_credential,
                platform_prefix=prefix,
                olts=mappings,
                trap_receiver=traps,
            )
        )
        hub_script = render_hub_sstp_peer(
            HubSstpPeer(tenant.slug, row.name, user, secret, _ip(row.overlay_ip), nat_ips)
        )
    else:
        isp_script = render_isp_script(
            IspTunnel(
                tenant=tenant.slug,
                router=row.name,
                private_key=secret,
                address=_ip(row.overlay_ip),
                hub_host=hub_host,
                hub_port=settings.tunnel_hub_port,
                hub_public_key=hub_credential,
                platform_prefix=prefix,
                olts=mappings,
                trap_receiver=traps,
            )
        )
        hub_script = render_hub_peer(
            HubPeer(
                tenant=tenant.slug,
                router=row.name,
                public_key=row.wg_public_key or "",
                address=_ip(row.overlay_ip),
                nat_ips=nat_ips,
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
        "transport": row.transport,
        "wg_public_key": row.wg_public_key,
        "ppp_user": row.ppp_user,
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
    """Da de alta un MikroTik del ISP y devuelve sus scripts. Su credencial no se guarda."""
    ctx.require("tunnel:write")
    transport = transport_for(body.routeros_version, body.transport)
    _hub(state, transport)
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
                routeros_version=body.routeros_version,
            )
            secret = _credentials(row, tenant, transport)
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
                after={"name": row.name, "overlay_ip": str(row.overlay_ip), **_identity(row)},
                source_ip=ctx.client_ip,
            )
            return await _scripts(session, state, tenant, row, secret)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un router con ese nombre") from exc


@router.post("/{router_id}/script", response_model=RouterScriptOut)
async def rotate_and_render(
    router_id: UUID, ctx: Tenant, state: State, body: RouterUpdate | None = None
) -> RouterScriptOut:
    """Rota la credencial del router y devuelve los scripts con las OLT actuales.

    Con otra ``routeros_version`` (o ``transport``) cambia de transporte: así un router v6
    dado de alta como v7 pasa a SSTP sin borrarlo ni cambiar su IP en el túnel.
    """
    ctx.require("tunnel:write")
    async with ctx.session() as session:
        row = await session.get(TunnelRouter, router_id)
        tenant = await session.get(TenantRow, ctx.tenant_id)
        if row is None or tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Router no encontrado")
        if body is not None and (body.routeros_version is not None or body.transport is not None):
            version = body.routeros_version or row.routeros_version
            transport = transport_for(version, body.transport)
        else:
            version, transport = row.routeros_version, row.transport  # type: ignore[assignment]
        _hub(state, transport)
        before = _identity(row)
        row.routeros_version = version
        secret = _credentials(row, tenant, transport)
        await session.flush()
        await audit(
            session,
            tenant_id=ctx.tenant_id,
            actor=ctx.actor,
            action="tunnel.router.rotate_keys",
            target_type="tunnel_router",
            target_id=str(row.id),
            before=before,
            after={**_identity(row), "at": datetime.now(UTC).isoformat()},
            source_ip=ctx.client_ip,
        )
        return await _scripts(session, state, tenant, row, secret)
