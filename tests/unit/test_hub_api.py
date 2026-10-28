"""Alta de routers en el concentrador por la API de RouterOS (con una API de mentira)."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from typing import Any

import pytest

from olterra.tunnel.hub_api import HubApiConfig, HubApiError, register_router
from olterra.tunnel.routeros import HubPeer, HubSstpPeer, ScriptError, render_hub_bootstrap
from olterra.tunnel.sstp import generate_password
from olterra.tunnel.wireguard import generate_keypair

CONFIG = HubApiConfig(host="198.18.0.1", username="olterra-api", password="x" * 32)
TAG = "olterra:isp-piloto:BNG-1"


class FakePath:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.added: list[dict[str, Any]] = []

    def __iter__(self) -> Any:
        return iter(list(self.rows))

    def remove(self, item_id: str) -> None:
        self.rows[:] = [r for r in self.rows if r[".id"] != item_id]

    def add(self, **values: Any) -> str:
        self.added.append(values)
        return "*new"


class FakeApi:
    def __init__(self) -> None:
        self.paths = {
            ("interface", "wireguard", "peers"): FakePath(
                [
                    {".id": "*1", "comment": TAG, "public-key": "vieja"},
                    {".id": "*2", "comment": "ispwatch:otro", "public-key": "ajena"},
                ]
            ),
            ("ppp", "secret"): FakePath([{".id": "*3", "comment": TAG, "name": "viejo"}]),
            ("ppp", "active"): FakePath([{".id": "*4", "name": "olterra-isp-piloto-BNG-1"}]),
        }
        self.closed = False
        self.login: tuple[Any, ...] = ()

    def path(self, *parts: str) -> FakePath:
        return self.paths[parts]

    def close(self) -> None:
        self.closed = True


def connect_to(api: FakeApi) -> Any:
    def connect(host: str, username: str, password: str, **kw: Any) -> FakeApi:
        api.login = (host, username, kw.get("port"))
        return api

    return connect


async def test_wireguard_router_replaces_only_its_own_peer() -> None:
    api = FakeApi()
    public = generate_keypair()[1]
    await register_router(
        CONFIG,
        HubPeer("isp-piloto", "BNG-1", public, IPv4Address("198.18.1.1")),
        connect=connect_to(api),
    )
    peers = api.paths[("interface", "wireguard", "peers")]
    assert [r[".id"] for r in peers.rows] == ["*2"]  # lo de ISPWatch no se toca
    assert peers.added == [
        {
            "interface": "olterra-hub",
            "public-key": public,
            "allowed-address": "198.18.1.1/32",
            "comment": TAG,
        }
    ]
    assert api.paths[("ppp", "secret")].rows == []  # si venía por SSTP, se va
    assert api.login == ("198.18.0.1", "olterra-api", 8728) and api.closed


async def test_sstp_router_gets_its_secret_and_drops_the_old_session() -> None:
    api = FakeApi()
    password = generate_password()
    await register_router(
        CONFIG,
        HubSstpPeer(
            "isp-piloto",
            "BNG-1",
            "olterra-isp-piloto-BNG-1",
            password,
            IPv4Address("198.18.1.1"),
        ),
        connect=connect_to(api),
    )
    assert api.paths[("ppp", "secret")].added == [
        {
            "name": "olterra-isp-piloto-BNG-1",
            "password": password,
            "service": "sstp",
            "profile": "olterra-sstp",
            "remote-address": "198.18.1.1",
            "comment": TAG,
        }
    ]
    assert api.paths[("ppp", "active")].rows == []
    assert [r[".id"] for r in api.paths[("interface", "wireguard", "peers")].rows] == ["*2"]


async def test_failures_become_hub_api_errors() -> None:
    def refuse(*args: Any, **kw: Any) -> Any:
        raise ConnectionRefusedError("connection refused")

    public = generate_keypair()[1]
    with pytest.raises(HubApiError, match="concentrador"):
        await register_router(
            CONFIG,
            HubPeer("isp-piloto", "BNG-1", public, IPv4Address("198.18.1.1")),
            connect=refuse,
        )
    with pytest.raises(HubApiError):  # validado igual que el bloque manual
        await register_router(
            CONFIG,
            HubPeer('isp"x', "BNG-1", public, IPv4Address("198.18.1.1")),
            connect=refuse,
        )


def bootstrap(**kw: Any) -> str:
    return render_hub_bootstrap(
        listen_port=13231,
        hub_address=IPv4Address("198.18.0.1"),
        platform_prefix=IPv4Network("198.18.0.0/24"),
        peer_pool=IPv4Network("198.18.0.0/16"),
        nat_pool=IPv4Network("198.19.0.0/16"),
        **kw,
    )


def test_bootstrap_creates_the_api_user_only_for_the_server() -> None:
    password = generate_password()
    script = bootstrap(api_password=password, api_source=IPv4Address("198.18.0.2"))
    assert "/user group add name=olterra-api policy=api,read,write" in script
    assert (
        f'/user add name=olterra-api group=olterra-api password="{password}"'
        ' address=198.18.0.2/32 comment="olterra-hub"'
    ) in script
    assert '/ip service set $apisvc address=($csv . "198.18.0.2/32")' in script
    assert (
        "chain=input in-interface=olterra-hub src-address=198.18.0.2 protocol=tcp"
        ' dst-port=$apiport action=accept comment="olterra-hub: API de Olterra"'
    ) in script
    assert "olterra-api" not in bootstrap()  # sin clave, sin usuario de API
    for bad in (
        {"api_password": password, "api_source": IPv4Address("10.0.0.2")},
        {"api_password": password},
        {"api_password": "corta", "api_source": IPv4Address("198.18.0.2")},
    ):
        with pytest.raises(ScriptError):
            bootstrap(**bad)
