"""Alta de los routers de los ISP en el concentrador (CHR) por la API de RouterOS.

Antes el operador pegaba en el CHR el bloque "Alta en el concentrador" cada vez que un ISP
agregaba o rotaba un MikroTik. Ahora la API de Olterra lo hace sola con un usuario propio del
CHR (``olterra-api``) que solo entra desde el servidor de Olterra por el túnel (lo crea
``olterra-admin concentrador``). Hace lo mismo que el bloque: borra lo que el router tenía
(peer WireGuard, secreto SSTP y su sesión) y crea lo nuevo, todo marcado con su comentario.

Con la puerta, el concentrador solo conoce la IP del router en el túnel: las OLT no tocan el
CHR. Si la API del CHR no está configurada o falla, la interfaz muestra el bloque para pegarlo
a mano, como antes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from olterra.tunnel.routeros import (
    HubPeer,
    HubSstpPeer,
    ScriptError,
    render_hub_peer,
    render_hub_sstp_peer,
)


class HubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class HubApiConfig:
    host: str
    username: str
    password: str
    port: int = 8728
    timeout: float = 10.0


def _remove_where(path: Any, field: str, value: str) -> None:
    for item in list(path):
        if item.get(field) == value:
            path.remove(item[".id"])


def _register_sync(config: HubApiConfig, peer: HubPeer | HubSstpPeer, connect: Any) -> None:
    api = connect(
        config.host, config.username, config.password, port=config.port, timeout=config.timeout
    )
    try:
        tag = f"olterra:{peer.tenant}:{peer.router}"
        peers = api.path("interface", "wireguard", "peers")
        secrets = api.path("ppp", "secret")
        _remove_where(peers, "comment", tag)
        _remove_where(secrets, "comment", tag)
        if isinstance(peer, HubSstpPeer):
            _remove_where(api.path("ppp", "active"), "name", peer.user)
            secrets.add(
                **{
                    "name": peer.user,
                    "password": peer.password,
                    "service": "sstp",
                    "profile": peer.profile,
                    "remote-address": str(peer.address),
                    "comment": tag,
                }
            )
        else:
            peers.add(
                **{
                    "interface": peer.hub_interface,
                    "public-key": peer.public_key,
                    "allowed-address": f"{peer.address}/32",
                    "comment": tag,
                }
            )
    finally:
        api.close()


async def register_router(
    config: HubApiConfig, peer: HubPeer | HubSstpPeer, connect: Any = None
) -> None:
    """Da de alta (o reemplaza) el router en el concentrador. ``HubApiError`` si no se pudo."""
    # Las mismas validaciones que el bloque manual: nada sin validar llega al CHR.
    try:
        if isinstance(peer, HubSstpPeer):
            render_hub_sstp_peer(peer)
        else:
            render_hub_peer(peer)
    except ScriptError as exc:
        raise HubApiError(str(exc)) from exc
    if connect is None:
        import librouteros

        connect = librouteros.connect
    try:
        await asyncio.to_thread(_register_sync, config, peer, connect)
    except Exception as exc:
        raise HubApiError(
            f"No se pudo dar de alta el router en el concentrador ({config.host}): {exc}"
        ) from exc
