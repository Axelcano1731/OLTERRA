from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from itertools import pairwise

import pytest

from olterra.tunnel.addressing import AddressPlan, AddressPlanError
from olterra.tunnel.routeros import (
    HubPeer,
    HubSstpPeer,
    IspTunnel,
    RouterGateway,
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


GATEWAY = RouterGateway(password="Puerta0lterra" + "x" * 19)


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
        "gateway": GATEWAY,
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
    # Sin reglas por OLT: las del script viejo se borran y no se crean otras.
    assert '/ip firewall nat remove [find where comment~"^olterra"]' in script
    assert "dst-nat" not in script and "masquerade" not in script
    assert "place-before=[:pick $ffil 0]" in script
    # Script ASCII: RouterOS no siempre muestra bien las tildes.
    assert script.isascii()


def test_isp_script_makes_the_mikrotik_a_gateway() -> None:
    """El usuario olterra solo entra por SSH desde la plataforma y no puede cambiar nada."""
    script = render_isp_script(tunnel())
    lines = script.splitlines()
    assert (
        '/user group add name=olterra policy=ssh,read comment="olterra:isp-piloto:puerta"' in lines
    )
    assert (
        f'/user add name=olterra group=olterra password="{GATEWAY.password}"'
        ' address=198.18.0.0/24 comment="olterra:isp-piloto:puerta"'
    ) in lines
    # Correrlo de nuevo no duplica el usuario ni el grupo.
    assert lines.index('/user remove [find where name="olterra"]') < lines.index(
        '/user group remove [find where name="olterra"]'
    )
    # Reenvío SSH solo si estaba apagado; en v7 "local" y, si no lo acepta, "yes".
    assert ':local fwd "local"' in lines and ':local fwdalt "yes"' in lines
    assert ':if (($fw = "no") || ($fw = "false")) do={' in lines
    # SSH abierto a la plataforma por el túnel, en el puerto que tenga el router.
    assert (
        "chain=input in-interface=$ifn src-address=198.18.0.0/24 protocol=tcp dst-port=$sshport"
        ' action=accept comment="olterra:isp-piloto:puerta"'
    ) in script
    # Si el SSH estaba apagado se enciende solo para la plataforma; si estaba limitado, se agrega.
    assert (
        ':local on [:parse ("/ip service set " . $svc . " disabled=no " . $svcprop'
        ' . "=198.18.0.0/24")]'
    ) in script
    assert (
        ':local add [:parse ("/ip service set " . $svc . " " . $svcprop . "=" . $csv'
        ' . "198.18.0.0/24")]'
    ) in script
    # RouterOS 7.24: /ip service también lista las conexiones activas (se toma el primero) y la
    # lista se llama available-from; abierto a todos (0.0.0.0/0) no se toca.
    assert ':local svc [:pick [/ip service find where name="ssh"] 0]' in script
    assert 'available-from]")]' in script
    assert '[:typeof [:find $csv "0.0.0.0/0"]] = "nil"' in script
    # El SSH puede estar en cualquier puerto: desde el túnel, el 22 se redirige al real.
    assert (
        "chain=dstnat in-interface=$ifn src-address=198.18.0.0/24 dst-address-type=local"
        " protocol=tcp dst-port=22 action=redirect to-ports=$sshport"
    ) in script
    assert "Las OLT nuevas funcionan solas" in script


def test_gateway_ssh_port_and_password_are_validated() -> None:
    script = render_isp_script(
        tunnel(gateway=RouterGateway(password=GATEWAY.password, ssh_port=2222))
    )
    assert "dst-port=2222 action=redirect to-ports=$sshport" in script
    for bad in (
        RouterGateway(password="corta"),
        RouterGateway(password=GATEWAY.password[:-1] + '"'),  # una comilla rompe la cadena
        RouterGateway(password=GATEWAY.password, ssh_port=0),
        RouterGateway(password=GATEWAY.password, user="olterra admin"),
    ):
        with pytest.raises(ScriptError):
            render_isp_script(tunnel(gateway=bad))


@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant": 'isp" ; /system reset-configuration'},
        {"router": "BNG 1"},
        {"hub_host": "hub.olterra.co; /ip service enable telnet"},
        {"hub_public_key": "x" * 44},
        {"address": IPv4Address("198.18.0.5")},  # dentro del prefijo de la plataforma
    ],
)
def test_isp_script_rejects_unsafe_input(overrides: dict[str, object]) -> None:
    with pytest.raises((ScriptError, InvalidKey)):
        render_isp_script(tunnel(**overrides))


def test_hub_scripts() -> None:
    _, public = generate_keypair()
    peer = render_hub_peer(HubPeer("isp-piloto", "BNG-1", public, IPv4Address("198.18.1.1")))
    # Solo la IP del router: sus OLT se alcanzan desde él, así que una OLT nueva no cambia nada.
    assert "allowed-address=198.18.1.1/32 " in peer
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
        "gateway": GATEWAY,
    }
    return SstpTunnel(**(values | overrides))  # type: ignore[arg-type]


def test_sstp_script_for_routeros_v6() -> None:
    tunnel = sstp_tunnel()
    script = render_isp_sstp_script(tunnel)
    assert script.isascii()
    assert "wireguard" not in script  # v6 no la tiene: el script no la nombra
    assert (
        "/interface sstp-client add name=$ifn connect-to=203.0.113.10:4443"
        f' user="olterra-isp-piloto-CORE_VIOTA" password="{tunnel.password}"'
    ) in script
    assert "verify-server-certificate=yes" in script
    # RouterOS 6 no conoce estos parámetros de v7: uno solo corta todo el script.
    [client] = [line for line in script.splitlines() if "sstp-client add" in line]
    for v7_only in (" port=", "tls-version", "pfs=", "ciphers"):
        assert v7_only not in client
    # La CA va en una sola línea del script, con los saltos como \n de RouterOS.
    [contents] = [line for line in script.splitlines() if "contents=" in line]
    assert contents.count("-----BEGIN CERTIFICATE-----\\n") == 1
    assert "/certificate import file-name=olterra-ca.txt" in script
    # La misma puerta que en v7; el reenvío prueba primero "yes" (v6) y luego "local".
    assert "/user group add name=olterra policy=ssh,read" in script
    assert ':local fwd "yes"' in script and ':local fwdalt "local"' in script
    assert "dst-nat" not in script
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
        )
    )
    assert (
        '/ppp secret add name="olterra-isp-piloto-CORE_VIOTA"'
        f' password="{password}" service=sstp profile=olterra-sstp remote-address=198.18.1.1'
        ' comment="olterra:isp-piloto:CORE_VIOTA"'
    ) in peer
    assert "routes=" not in peer
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
    # Correrlo de nuevo no corta el túnel: la dirección y las rutas solo cambian si están mal.
    assert '/ip address remove [find where comment="olterra-hub" and address!="198.18.0.1/24"]' in (
        bootstrap
    )
    assert '/ip route remove [find where comment="olterra-hub"]' not in bootstrap
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
