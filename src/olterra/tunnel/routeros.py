"""Scripts RouterOS del túnel: el del router del ISP y los del concentrador.

Reglas aprendidas en ISPWatch que este generador respeta:

- WireGuard existe desde RouterOS 7.1; en v6 no hay. El script lo verifica primero
  y se detiene con un mensaje claro (la sintaxis con espacios, no con ``/``, es
  para que ese chequeo corra también en v6).
- El ``listen-port`` del lado del ISP NO se fija: 13231 es el puerto por defecto y
  lo usa Back To Home. El script busca uno libre; el que importa es el del
  concentrador (``endpoint-port``), porque el router es quien disca.
- Correrlo dos veces no duplica nada: borra y recrea solo lo marcado ``olterra``.
- Si algo falla, falla a la vista (``/import`` muestra la línea); no hay
  ``on-error`` que se trague errores.

Todo valor que entra al script pasa por validación estricta: el script se pega en
el router del cliente, una inyección ahí sería grave.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv4Network, ip_address

from olterra.tunnel.wireguard import validate_key

_LABEL = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


class ScriptError(ValueError):
    pass


def _label(value: str, what: str) -> str:
    if not _LABEL.fullmatch(value):
        raise ScriptError(f"{what} solo admite letras, números, '.', '-' y '_' (máx. 32)")
    return value


def _host(value: str) -> str:
    try:
        return str(ip_address(value))
    except ValueError:
        if _HOSTNAME.fullmatch(value):
            return value
    raise ScriptError("El concentrador debe ser una IP o un nombre DNS válido")


@dataclass(frozen=True)
class OltMapping:
    name: str
    real_ip: IPv4Address
    nat_ip: IPv4Address


@dataclass(frozen=True)
class IspTunnel:
    tenant: str
    router: str
    private_key: str
    address: IPv4Address
    hub_host: str
    hub_port: int
    hub_public_key: str
    platform_prefix: IPv4Network
    olts: Sequence[OltMapping] = ()
    trap_receiver: IPv4Address | None = None
    interface: str = "olterra"
    first_listen_port: int = 13241
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> None:
        _label(self.tenant, "El tenant")
        _label(self.router, "El nombre del router")
        _label(self.interface, "El nombre de la interfaz")
        _host(self.hub_host)
        validate_key(self.private_key)
        validate_key(self.hub_public_key)
        if not 1 <= self.hub_port <= 65535 or not 1024 <= self.first_listen_port <= 65000:
            raise ScriptError("Puerto fuera de rango")
        if self.address in self.platform_prefix:
            raise ScriptError("La IP del router no puede estar dentro del prefijo de la plataforma")
        if self.trap_receiver is not None and self.trap_receiver not in self.platform_prefix:
            raise ScriptError("El receptor de traps debe estar dentro del prefijo de la plataforma")
        names, real_ips, nat_ips = set(), set(), set()
        for olt in self.olts:
            _label(olt.name, "El nombre de la OLT")
            if olt.name in names or olt.real_ip in real_ips or olt.nat_ip in nat_ips:
                raise ScriptError(f"OLT repetida: {olt.name}")
            if olt.nat_ip == olt.real_ip:
                raise ScriptError(f"{olt.name}: la IP NAT no puede ser la misma IP real")
            names.add(olt.name)
            real_ips.add(olt.real_ip)
            nat_ips.add(olt.nat_ip)


class _Script:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, line: str = "") -> None:
        self.lines.append(line)

    def add_first(self, menu: str, ids_var: str, args: str) -> None:
        """Agrega una regla antes de la primera regla estática existente (o al final si no hay)."""
        self.add(
            f":if ([:len ${ids_var}] > 0) do={{ {menu} add {args} place-before=[:pick ${ids_var} 0] }}"
            f" else={{ {menu} add {args} }}"
        )

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def render_isp_script(tunnel: IspTunnel) -> str:
    tunnel.validate()
    tag = f"olterra:{tunnel.tenant}"
    prefix = str(tunnel.platform_prefix)
    s = _Script()
    s.add("# " + "=" * 74)
    s.add("# Olterra - tunel de gestion hacia la plataforma")
    s.add(
        f"# ISP: {tunnel.tenant}   Router: {tunnel.router}   Generado: {tunnel.generated_at:%Y-%m-%d %H:%M} UTC"
    )
    s.add("#")
    s.add("# 1. Verifica RouterOS v7 (WireGuard no existe en v6).")
    s.add("# 2. Crea la interfaz WireGuard y la conecta al concentrador de Olterra.")
    s.add("# 3. Publica cada OLT con una IP unica de la plataforma (NAT 1:1).")
    s.add("# 4. Permite solo SSH (22) y SNMP (161) desde la plataforma hacia cada OLT,")
    s.add("#    y los traps SNMP (162) de la OLT hacia la plataforma.")
    s.add("# Se puede correr varias veces: borra y recrea solo lo marcado 'olterra'.")
    s.add("# Contiene una llave privada: no lo comparta ni lo guarde en un chat.")
    s.add("# " + "=" * 74)
    s.add("{")
    s.add(":local ver [/system resource get version]")
    s.add(':local major [:tonum [:pick $ver 0 [:find $ver "."]]]')
    s.add(
        ':if ($major < 7) do={ :error ("Olterra: WireGuard requiere RouterOS v7 y este router tiene "'
        ' . $ver . ". Actualice RouterOS o use el agente Olterra.") }'
    )
    s.add(f':local ifn "{tunnel.interface}"')
    s.add(":if ([:len [/interface wireguard find where name=$ifn]] = 0) do={")
    s.add(f"  :local p {tunnel.first_listen_port}")
    s.add(
        "  :while ([:len [/interface wireguard find where listen-port=$p]] > 0) do={ :set p ($p + 1) }"
    )
    s.add(
        f'  /interface wireguard add name=$ifn listen-port=$p mtu=1420 private-key="{tunnel.private_key}"'
        f' comment="{tag}"'
    )
    s.add("} else={")
    s.add(f'  /interface wireguard set [find where name=$ifn] private-key="{tunnel.private_key}"')
    s.add("}")
    s.add("/interface wireguard peers remove [find where interface=$ifn]")
    s.add(
        f'/interface wireguard peers add interface=$ifn public-key="{tunnel.hub_public_key}"'
        f" endpoint-address={_host(tunnel.hub_host)} endpoint-port={tunnel.hub_port}"
        f' allowed-address={prefix} persistent-keepalive=25s comment="{tag}"'
    )
    s.add('/ip address remove [find where comment~"^olterra"]')
    s.add(f'/ip address add address={tunnel.address}/32 interface=$ifn comment="{tag}"')
    s.add('/ip route remove [find where comment~"^olterra"]')
    s.add(f'/ip route add dst-address={prefix} gateway=$ifn comment="{tag}"')
    s.add('/ip firewall nat remove [find where comment~"^olterra"]')
    s.add('/ip firewall filter remove [find where comment~"^olterra"]')
    s.add(":local fnat [/ip firewall nat find where dynamic=no]")
    s.add(":local ffil [/ip firewall filter find where dynamic=no]")
    s.add_first(
        "/ip firewall filter",
        "ffil",
        f'chain=forward out-interface=$ifn connection-state=established,related action=accept comment="{tag}"',
    )
    for olt in tunnel.olts:
        olt_tag = f"{tag}:olt:{olt.name}"
        s.add_first(
            "/ip firewall nat",
            "fnat",
            f"chain=dstnat in-interface=$ifn dst-address={olt.nat_ip} action=dst-nat"
            f' to-addresses={olt.real_ip} comment="{olt_tag}"',
        )
        s.add_first(
            "/ip firewall nat",
            "fnat",
            f"chain=srcnat src-address={prefix} dst-address={olt.real_ip} action=masquerade"
            f' comment="{olt_tag}"',
        )
        for protocol, port in (("tcp", 22), ("udp", 161)):
            s.add_first(
                "/ip firewall filter",
                "ffil",
                f"chain=forward in-interface=$ifn dst-address={olt.real_ip} protocol={protocol}"
                f' dst-port={port} action=accept comment="{olt_tag}"',
            )
        if tunnel.trap_receiver is not None:
            s.add_first(
                "/ip firewall nat",
                "fnat",
                f"chain=srcnat src-address={olt.real_ip} dst-address={tunnel.trap_receiver}"
                f" protocol=udp dst-port=162 action=src-nat to-addresses={olt.nat_ip}"
                f' comment="{olt_tag}:traps"',
            )
            s.add_first(
                "/ip firewall filter",
                "ffil",
                f"chain=forward src-address={olt.real_ip} dst-address={tunnel.trap_receiver}"
                f' protocol=udp dst-port=162 out-interface=$ifn action=accept comment="{olt_tag}:traps"',
            )
    s.add(
        ':put "Olterra: tunel configurado. En /interface wireguard peers, last-handshake debe ser reciente."'
    )
    s.add("}")
    return s.text()


@dataclass(frozen=True)
class HubPeer:
    tenant: str
    router: str
    public_key: str
    address: IPv4Address
    nat_ips: Sequence[IPv4Address] = ()
    hub_interface: str = "olterra-hub"


def render_hub_peer(peer: HubPeer) -> str:
    """Alta (o reemplazo) del router de un ISP en el concentrador."""
    _label(peer.tenant, "El tenant")
    _label(peer.router, "El nombre del router")
    _label(peer.hub_interface, "La interfaz del concentrador")
    validate_key(peer.public_key)
    tag = f"olterra:{peer.tenant}:{peer.router}"
    allowed = ",".join(f"{ip}/32" for ip in (peer.address, *peer.nat_ips))
    return (
        f"# Olterra - router {peer.router} del ISP {peer.tenant} en el concentrador\n"
        "{\n"
        f'/interface wireguard peers remove [find where comment="{tag}"]\n'
        f'/interface wireguard peers add interface={peer.hub_interface} public-key="{peer.public_key}"'
        f' allowed-address={allowed} comment="{tag}"\n'
        "}\n"
    )


def render_hub_bootstrap(
    *,
    listen_port: int,
    hub_address: IPv4Address,
    platform_prefix: IPv4Network,
    peer_pool: IPv4Network,
    nat_pool: IPv4Network,
    interface: str = "olterra-hub",
) -> str:
    """Configuración inicial del concentrador (una sola vez).

    La llave privada la genera el propio RouterOS al crear la interfaz; su pública
    va a ``OLTERRA_TUNNEL_HUB_PUBLIC_KEY``. El aislamiento entre ISP se hace aquí:
    la plataforma llega a todos, los ISP no se ven entre sí.
    """
    _label(interface, "La interfaz del concentrador")
    if hub_address not in platform_prefix:
        raise ScriptError("La IP del concentrador va dentro del prefijo de la plataforma")
    if not 1 <= listen_port <= 65535:
        raise ScriptError("Puerto fuera de rango")
    tag = "olterra-hub"
    s = _Script()
    s.add("# Olterra - configuracion inicial del concentrador (RouterOS v7, una sola vez)")
    s.add("{")
    s.add(f':if ([:len [/interface wireguard find where name="{interface}"]] = 0) do={{')
    s.add(
        f'  /interface wireguard add name={interface} listen-port={listen_port} mtu=1420 comment="{tag}"'
    )
    s.add("}")
    s.add(f'/ip address remove [find where comment="{tag}"]')
    s.add(
        f'/ip address add address={hub_address}/{platform_prefix.prefixlen} interface={interface} comment="{tag}"'
    )
    s.add(f'/ip route remove [find where comment="{tag}"]')
    s.add(f'/ip route add dst-address={peer_pool} gateway={interface} comment="{tag}"')
    s.add(f'/ip route add dst-address={nat_pool} gateway={interface} comment="{tag}"')
    s.add(f'/ip firewall filter remove [find where comment~"^{tag}"]')
    s.add(":local ffil [/ip firewall filter find where dynamic=no]")
    rules = [
        f'chain=input protocol=udp dst-port={listen_port} action=accept comment="{tag}: WireGuard"',
        f"chain=forward in-interface={interface} out-interface={interface} src-address={platform_prefix}"
        f' action=accept comment="{tag}: plataforma hacia los ISP"',
        f"chain=forward in-interface={interface} out-interface={interface} dst-address={platform_prefix}"
        f' protocol=udp dst-port=162 action=accept comment="{tag}: traps hacia la plataforma"',
        f"chain=forward in-interface={interface} out-interface={interface}"
        f' connection-state=established,related action=accept comment="{tag}: respuestas"',
        f"chain=forward in-interface={interface} out-interface={interface} action=drop"
        f' comment="{tag}: sin ruteo entre ISP"',
    ]
    for rule in rules:
        s.add_first("/ip firewall filter", "ffil", rule)
    s.add("}")
    return s.text()
