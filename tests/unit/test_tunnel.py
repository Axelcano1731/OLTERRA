from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from itertools import pairwise

import pytest

from olterra.tunnel.addressing import AddressPlan, AddressPlanError
from olterra.tunnel.routeros import (
    HubPeer,
    HubSstpPeer,
    IspTunnel,
    OltMapping,
    ScriptError,
    SstpTunnel,
    render_hub_bootstrap,
    render_hub_peer,
    render_hub_sstp_peer,
    render_isp_script,
    render_isp_sstp_script,
)
from olterra.tunnel.sstp import InvalidSstp, generate_password, normalize_ca, ppp_user
from olterra.tunnel.wireguard import InvalidKey, generate_keypair, public_from_private, validate_key
from tests.conftest import ca_pem

PLAN = AddressPlan.from_strings()


def test_default_address_plan_blocks() -> None:
    assert PLAN.tenant_peer_block(1) == IPv4Network(
        "198.18.1.0/28"
    )  # el /24 de la plataforma se salta
    assert PLAN.tenant_peer_block(2) == IPv4Network("198.18.1.16/28")
    assert PLAN.tenant_nat_block(1) == IPv4Network("198.19.0.0/26")
    assert PLAN.tenant_nat_block(2) == IPv4Network("198.19.0.64/26")
    assert PLAN.peer_address(1, 1) == IPv4Address("198.18.1.1")
    assert PLAN.nat_address(2, 0) == IPv4Address("198.19.0.64")
    assert PLAN.max_tenants == 1024


def test_tenant_blocks_never_overlap() -> None:
    peers = [PLAN.tenant_peer_block(i) for i in range(1, PLAN.max_tenants + 1)]
    nats = [PLAN.tenant_nat_block(i) for i in range(1, PLAN.max_tenants + 1)]
    platform = IPv4Network("198.18.0.0/24")
    assert len({str(p) for p in peers}) == len(peers)
    assert not any(p.overlaps(platform) for p in peers)
    assert all(a.network_address < b.network_address for a, b in pairwise(nats))
    assert all(n.subnet_of(IPv4Network("198.19.0.0/16")) for n in nats)


@pytest.mark.parametrize(
    "call",
    [
        lambda: PLAN.tenant_peer_block(0),
        lambda: PLAN.tenant_nat_block(PLAN.max_tenants + 1),
        lambda: PLAN.peer_address(1, 15),  # /28 deja 14 routers
        lambda: PLAN.nat_address(1, 64),  # /26 deja 64 OLT
        lambda: AddressPlan.from_strings(peer_pool="198.18.0.0/15", nat_pool="198.19.0.0/16"),
    ],
)
def test_address_plan_limits(call: object) -> None:
    with pytest.raises(AddressPlanError):
        call()  # type: ignore[operator]


def test_wireguard_keys() -> None:
    private, public = generate_keypair()
    assert public_from_private(private) == public
    assert validate_key(public) == public
    with pytest.raises(InvalidKey):
        validate_key("no-es-base64!")
    with pytest.raises(InvalidKey):
        validate_key("YWJj")  # base64 válido de 3 bytes


def tunnel(**overrides: object) -> IspTunnel:
    private, _ = generate_keypair()
    _, hub_public = generate_keypair()
    values: dict[str, object] = {
        "tenant": "isp-piloto",
        "router": "BNG-1",
        "private_key": private,
        "address": IPv4Address("198.18.1.1"),
        "hub_host": "hub.olterra.co",
        "hub_port": 13231,
        "hub_public_key": hub_public,
        "platform_prefix": IPv4Network("198.18.0.0/24"),
        "olts": [OltMapping("OLT-1", IPv4Address("192.168.8.200"), IPv4Address("198.19.0.0"))],
        "trap_receiver": IPv4Address("198.18.0.2"),
    }
    values.update(overrides)
    return IspTunnel(**values)  # type: ignore[arg-type]


def test_isp_script_contents() -> None:
    t = tunnel()
    script = render_isp_script(t)
    # Verificación de v7 con sintaxis de espacios (corre también en v6) y bloque único.
    assert (
        script.splitlines()[script.splitlines().index("{") + 1]
        == ":local ver [/system resource get version]"
    )
    assert ":if ($major < 7) do={ :error" in script
    assert script.rstrip().endswith("}")
    # Puerto local buscado, no fijo; el que se fija es el del concentrador.
    assert ":while ([:len [/interface wireguard find where listen-port=$p]] > 0)" in script
    assert "endpoint-address=hub.olterra.co endpoint-port=13231" in script
    assert f'private-key="{t.private_key}"' in script
    # NAT 1:1, masquerade hacia la OLT y traps salientes con la IP única de la OLT.
    assert (
        "chain=dstnat in-interface=$ifn dst-address=198.19.0.0 action=dst-nat to-addresses=192.168.8.200"
        in script
    )
    assert (
        "chain=srcnat src-address=198.18.0.0/24 dst-address=192.168.8.200 action=masquerade"
        in script
    )
    assert "dst-port=162 action=src-nat to-addresses=198.19.0.0" in script
    # Solo SSH y SNMP hacia la OLT.
    assert "protocol=tcp dst-port=22 action=accept" in script
    assert "protocol=udp dst-port=161 action=accept" in script
    assert "dst-port=23" not in script and "dst-port=80" not in script
    # Idempotente: limpia lo propio antes de crear y ubica las reglas antes de las del ISP.
    assert '/ip firewall nat remove [find where comment~"^olterra"]' in script
    assert "place-before=[:pick $ffil 0]" in script
    # Script ASCII: RouterOS no siempre muestra bien las tildes.
    assert script.isascii()


def test_isp_script_without_traps_or_olts() -> None:
    script = render_isp_script(tunnel(olts=[], trap_receiver=None))
    assert "dst-nat" not in script
    assert "dst-port=162" not in script


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant": 'isp" ; /system reset-configuration'},
        {"router": "BNG 1"},
        {"hub_host": "hub.olterra.co; /ip service enable telnet"},
        {"hub_public_key": "x" * 44},
        {"address": IPv4Address("198.18.0.5")},  # dentro del prefijo de la plataforma
        {"trap_receiver": IPv4Address("10.0.0.1")},
        {"olts": [OltMapping('OLT"1', IPv4Address("192.168.8.200"), IPv4Address("198.19.0.0"))]},
        {
            "olts": [
                OltMapping("A", IPv4Address("192.168.8.200"), IPv4Address("198.19.0.0")),
                OltMapping("B", IPv4Address("192.168.8.201"), IPv4Address("198.19.0.0")),
            ]
        },
    ],
)
def test_isp_script_rejects_unsafe_input(overrides: dict[str, object]) -> None:
    with pytest.raises((ScriptError, InvalidKey)):
        render_isp_script(tunnel(**overrides))


def test_hub_scripts() -> None:
    _, public = generate_keypair()
    peer = render_hub_peer(
        HubPeer(
            "isp-piloto", "BNG-1", public, IPv4Address("198.18.1.1"), [IPv4Address("198.19.0.0")]
        )
    )
    assert "allowed-address=198.18.1.1/32,198.19.0.0/32" in peer
    assert 'comment="olterra:isp-piloto:BNG-1"' in peer

    bootstrap = render_hub_bootstrap(
        listen_port=13231,
        hub_address=IPv4Address("198.18.0.1"),
        platform_prefix=IPv4Network("198.18.0.0/24"),
        peer_pool=IPv4Network("198.18.0.0/16"),
        nat_pool=IPv4Network("198.19.0.0/16"),
    )
    lines = bootstrap.splitlines()
    drop = next(i for i, line in enumerate(lines) if "sin ruteo entre ISP" in line)
    accept_platform = next(i for i, line in enumerate(lines) if "plataforma hacia los ISP" in line)
    assert accept_platform < drop  # el aislamiento va después de las excepciones
    # Los ISP no llegan a los servicios del concentrador por el túnel; la plataforma sí.
    assert (
        "chain=input in-interface-list=olterra-tuneles src-address=!198.18.0.0/24 action=drop"
        in bootstrap
    )
    assert "sstp" not in bootstrap  # sin puerto SSTP, solo WireGuard
    with pytest.raises(ScriptError):
        render_hub_bootstrap(
            listen_port=13231,
            hub_address=IPv4Address("10.0.0.1"),
            platform_prefix=IPv4Network("198.18.0.0/24"),
            peer_pool=IPv4Network("198.18.0.0/16"),
            nat_pool=IPv4Network("198.19.0.0/16"),
        )


# --- SSTP (RouterOS v6) ----------------------------------------------------------------

PLATFORM = IPv4Network("198.18.0.0/24")


def sstp_tunnel(**overrides: object) -> SstpTunnel:
    values: dict[str, object] = {
        "tenant": "isp-piloto",
        "router": "CORE_VIOTA",
        "user": ppp_user("isp-piloto", "CORE_VIOTA"),
        "password": generate_password(),
        "hub_host": "203.0.113.10",
        "hub_port": 4443,
        "ca_pem": ca_pem(),
        "platform_prefix": PLATFORM,
        "olts": [OltMapping("OLT-1", IPv4Address("192.168.8.200"), IPv4Address("198.19.0.0"))],
        "trap_receiver": IPv4Address("198.18.0.2"),
    }
    return SstpTunnel(**(values | overrides))  # type: ignore[arg-type]


def test_sstp_script_for_routeros_v6() -> None:
    tunnel = sstp_tunnel()
    script = render_isp_sstp_script(tunnel)
    assert script.isascii()
    assert "wireguard" not in script  # v6 no la tiene: el script no la nombra
    assert (
        "/interface sstp-client add name=$ifn connect-to=203.0.113.10 port=4443"
        f' user="olterra-isp-piloto-CORE_VIOTA" password="{tunnel.password}"'
    ) in script
    assert "verify-server-certificate=yes" in script
    # La CA va en una sola línea del script, con los saltos como \n de RouterOS.
    [contents] = [line for line in script.splitlines() if "contents=" in line]
    assert contents.count("-----BEGIN CERTIFICATE-----\\n") == 1
    assert "/certificate import file-name=olterra-ca.txt" in script
    # Lo mismo que en v7 para cada OLT: NAT 1:1, SSH/SNMP y traps.
    assert "dst-address=198.19.0.0 action=dst-nat to-addresses=192.168.8.200" in script
    assert "dst-address=192.168.8.200 protocol=tcp dst-port=22 action=accept" in script
    assert "to-addresses=198.19.0.0" in script  # los traps salen con la IP NAT de la OLT
    assert "/ip route add dst-address=198.18.0.0/24 gateway=$ifn" in script


def test_sstp_ca_from_env_line() -> None:
    pem = ca_pem()
    body = "".join(line for line in pem.splitlines() if "CERTIFICATE" not in line)
    assert normalize_ca(body) == normalize_ca(pem)  # en .env cabe en una línea


@pytest.mark.parametrize(
    "overrides",
    [
        {"password": "corta"},
        {"password": "A" * 31 + '"'},  # una comilla rompería la cadena RouterOS
        {"user": "olterra isp"},
        {"ca_pem": "no es un certificado"},
        {"ca_pem": ca_pem(ca=False)},  # tiene que ser una CA
        {"hub_host": "concentrador;/system reset"},
    ],
)
def test_sstp_script_rejects_bad_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ScriptError):
        render_isp_sstp_script(sstp_tunnel(**overrides))


def test_sstp_credentials() -> None:
    assert ppp_user("isp-piloto", "BNG-1") == "olterra-isp-piloto-BNG-1"
    assert len({generate_password() for _ in range(20)}) == 20
    with pytest.raises(InvalidSstp):
        ppp_user("isp piloto", "BNG")


def test_hub_sstp_peer_and_switching_transport() -> None:
    password = generate_password()
    peer = render_hub_sstp_peer(
        HubSstpPeer(
            "isp-piloto",
            "CORE_VIOTA",
            "olterra-isp-piloto-CORE_VIOTA",
            password,
            IPv4Address("198.18.1.1"),
            [IPv4Address("198.19.0.0"), IPv4Address("198.19.0.1")],
        )
    )
    assert (
        '/ppp secret add name="olterra-isp-piloto-CORE_VIOTA"'
        f' password="{password}" service=sstp profile=olterra-sstp remote-address=198.18.1.1'
        ' routes="198.19.0.0/32,198.19.0.1/32"'
    ) in peer
    tag = 'comment="olterra:isp-piloto:CORE_VIOTA"'
    # Si venía por WireGuard, su peer se va; y al revés, el alta WireGuard borra el secreto.
    assert f"/interface wireguard peers remove [find where {tag}]" in peer
    public = generate_keypair()[1]
    wg = render_hub_peer(HubPeer("isp-piloto", "CORE_VIOTA", public, IPv4Address("198.18.1.1")))
    assert f"/ppp secret remove [find where {tag}]" in wg


def test_hub_bootstrap_with_sstp() -> None:
    bootstrap = render_hub_bootstrap(
        listen_port=13232,
        hub_address=IPv4Address("198.18.0.1"),
        platform_prefix=PLATFORM,
        peer_pool=IPv4Network("198.18.0.0/16"),
        nat_pool=IPv4Network("198.19.0.0/16"),
        sstp_port=4443,
        sstp_host="203.0.113.10",
    )
    assert bootstrap.isascii()
    assert "/interface sstp-server server set enabled=yes port=4443" in bootstrap
    assert "subject-alt-name=IP:203.0.113.10" in bootstrap
    assert "local-address=198.18.0.1 interface-list=olterra-tuneles" in bootstrap
    lines = bootstrap.splitlines()
    sstp = next(i for i, line in enumerate(lines) if "dst-port=4443 action=accept" in line)
    drop = next(i for i, line in enumerate(lines) if "sin ruteo entre ISP" in line)
    assert sstp < drop
    with pytest.raises(ScriptError):  # el certificado necesita la IP o el nombre público
        render_hub_bootstrap(
            listen_port=13232,
            hub_address=IPv4Address("198.18.0.1"),
            platform_prefix=PLATFORM,
            peer_pool=IPv4Network("198.18.0.0/16"),
            nat_pool=IPv4Network("198.19.0.0/16"),
            sstp_port=4443,
        )
