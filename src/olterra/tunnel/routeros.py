"""Scripts RouterOS del túnel: el del router del ISP y los del concentrador.

Dos transportes hacia el mismo concentrador (ARQUITECTURA, A.4):

- **WireGuard** para RouterOS 7.
- **SSTP** (PPP sobre TLS, TCP) para RouterOS 6, que no tiene WireGuard. No se usa
  L2TP/IPsec como en ISPWatch: muchos routers v6 ya tienen el L2TP de ISPWatch contra el
  mismo CHR y un segundo L2TP al mismo servidor choca en IPsec. El certificado de la CA
  de Olterra va dentro del script, así el router solo habla con el concentrador real.

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

from olterra.tunnel.sstp import InvalidSstp, normalize_ca, validate_password, validate_user
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
        _validate_olts(self.olts, self.trap_receiver, self.platform_prefix)


def _validate_olts(
    olts: Sequence[OltMapping], trap_receiver: IPv4Address | None, platform_prefix: IPv4Network
) -> None:
    if trap_receiver is not None and trap_receiver not in platform_prefix:
        raise ScriptError("El receptor de traps debe estar dentro del prefijo de la plataforma")
    names, real_ips, nat_ips = set(), set(), set()
    for olt in olts:
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
    _olt_rules(s, tag, prefix, tunnel.olts, tunnel.trap_receiver)
    s.add(
        ':put "Olterra: tunel configurado. En /interface wireguard peers, last-handshake debe ser reciente."'
    )
    s.add("}")
    return s.text()


def _olt_rules(
    s: _Script,
    tag: str,
    prefix: str,
    olts: Sequence[OltMapping],
    trap_receiver: IPv4Address | None,
) -> None:
    """NAT 1:1 y filtros de cada OLT detrás de la interfaz del túnel (``$ifn``).

    Igual para WireGuard y para SSTP: solo cambia qué interfaz es ``$ifn``.
    """
    s.add('/ip firewall nat remove [find where comment~"^olterra"]')
    s.add('/ip firewall filter remove [find where comment~"^olterra"]')
    s.add(":local fnat [/ip firewall nat find where dynamic=no]")
    s.add(":local ffil [/ip firewall filter find where dynamic=no]")
    s.add_first(
        "/ip firewall filter",
        "ffil",
        f'chain=forward out-interface=$ifn connection-state=established,related action=accept comment="{tag}"',
    )
    for olt in olts:
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
        if trap_receiver is not None:
            s.add_first(
                "/ip firewall nat",
                "fnat",
                f"chain=srcnat src-address={olt.real_ip} dst-address={trap_receiver}"
                f" protocol=udp dst-port=162 action=src-nat to-addresses={olt.nat_ip}"
                f' comment="{olt_tag}:traps"',
            )
            s.add_first(
                "/ip firewall filter",
                "ffil",
                f"chain=forward src-address={olt.real_ip} dst-address={trap_receiver}"
                f' protocol=udp dst-port=162 out-interface=$ifn action=accept comment="{olt_tag}:traps"',
            )


@dataclass(frozen=True)
class SstpTunnel:
    """Router del ISP con RouterOS v6: cliente SSTP (PPP sobre TLS, TCP) al concentrador."""

    tenant: str
    router: str
    user: str
    password: str
    hub_host: str
    hub_port: int
    ca_pem: str
    platform_prefix: IPv4Network
    olts: Sequence[OltMapping] = ()
    trap_receiver: IPv4Address | None = None
    interface: str = "olterra"
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> str:
        """Valida todo y devuelve la CA en PEM normalizado."""
        _label(self.tenant, "El tenant")
        _label(self.router, "El nombre del router")
        _label(self.interface, "El nombre de la interfaz")
        _host(self.hub_host)
        if not 1 <= self.hub_port <= 65535:
            raise ScriptError("Puerto fuera de rango")
        _validate_olts(self.olts, self.trap_receiver, self.platform_prefix)
        try:
            validate_user(self.user)
            validate_password(self.password)
            return normalize_ca(self.ca_pem)
        except InvalidSstp as exc:
            raise ScriptError(str(exc)) from exc


def render_isp_sstp_script(tunnel: SstpTunnel) -> str:
    """Script del router del ISP con RouterOS v6 (sirve igual en v7).

    El certificado de la CA va dentro del script: v6 no deja importarlo desde texto, así que
    se escribe en un archivo (``/file print file=`` + ``/file set contents=``) y se importa.
    """
    ca = tunnel.validate()
    tag = f"olterra:{tunnel.tenant}"
    prefix = str(tunnel.platform_prefix)
    ca_string = ca.replace("\n", "\\n")  # en una cadena RouterOS, \n es un salto de línea
    s = _Script()
    s.add("# " + "=" * 74)
    s.add("# Olterra - tunel de gestion hacia la plataforma (RouterOS v6, SSTP)")
    s.add(
        f"# ISP: {tunnel.tenant}   Router: {tunnel.router}   Generado: {tunnel.generated_at:%Y-%m-%d %H:%M} UTC"
    )
    s.add("#")
    s.add("# 1. Instala el certificado de Olterra: el router solo habla con el concentrador real.")
    s.add(f"# 2. Crea el cliente SSTP (TLS sobre TCP {tunnel.hub_port}) hacia el concentrador.")
    s.add("# 3. Publica cada OLT con una IP unica de la plataforma (NAT 1:1).")
    s.add("# 4. Permite solo SSH (22) y SNMP (161) desde la plataforma hacia cada OLT,")
    s.add("#    y los traps SNMP (162) de la OLT hacia la plataforma.")
    s.add("# Se puede correr varias veces: borra y recrea solo lo marcado 'olterra'.")
    s.add("# Contiene la clave del tunel: no lo comparta ni lo guarde en un chat.")
    s.add("# Subalo como archivo (Files) y corralo con /import: pegarlo en la consola lo corrompe.")
    s.add("# " + "=" * 74)
    s.add("{")
    s.add(f':local ifn "{tunnel.interface}"')
    s.add('/certificate remove [find where name~"^olterra-ca"]')
    s.add('/file remove [find where name~"^olterra-ca"]')
    s.add("/file print file=olterra-ca")
    s.add(":delay 2s")
    s.add(f'/file set [find where name~"^olterra-ca"] contents="{ca_string}"')
    s.add(":delay 1s")
    s.add('/certificate import file-name=olterra-ca.txt passphrase=""')
    s.add(":delay 1s")
    s.add('/file remove [find where name~"^olterra-ca"]')
    s.add('/certificate set [find where name~"^olterra-ca"] name=olterra-ca trusted=yes')
    s.add("/interface sstp-client remove [find where name=$ifn]")
    s.add(
        f"/interface sstp-client add name=$ifn connect-to={_host(tunnel.hub_host)}"
        f' port={tunnel.hub_port} user="{tunnel.user}" password="{tunnel.password}"'
        " profile=default-encryption authentication=mschap2 verify-server-certificate=yes"
        " verify-server-address-from-certificate=no add-default-route=no keepalive-timeout=60"
        f' comment="{tag}" disabled=no'
    )
    s.add('/ip route remove [find where comment~"^olterra"]')
    s.add(f'/ip route add dst-address={prefix} gateway=$ifn comment="{tag}"')
    _olt_rules(s, tag, prefix, tunnel.olts, tunnel.trap_receiver)
    s.add(
        ':put "Olterra: tunel SSTP configurado. En /interface sstp-client debe quedar conectado (R)."'
    )
    s.add("}")
    return s.text()


@dataclass(frozen=True)
class HubSstpPeer:
    tenant: str
    router: str
    user: str
    password: str
    address: IPv4Address
    nat_ips: Sequence[IPv4Address] = ()
    profile: str = "olterra-sstp"


def render_hub_sstp_peer(peer: HubSstpPeer) -> str:
    """Alta (o reemplazo) en el concentrador de un router del ISP que entra por SSTP.

    Las IP NAT de sus OLT van como ``routes`` del secreto PPP: el concentrador las enruta al
    túnel del router mientras está conectado. Si el router venía por WireGuard, se borra
    su peer (misma marca).
    """
    _label(peer.tenant, "El tenant")
    _label(peer.router, "El nombre del router")
    _label(peer.profile, "El perfil PPP")
    try:
        validate_user(peer.user)
        validate_password(peer.password)
    except InvalidSstp as exc:
        raise ScriptError(str(exc)) from exc
    tag = f"olterra:{peer.tenant}:{peer.router}"
    routes = ",".join(f"{ip}/32" for ip in peer.nat_ips)
    secret = (
        f'/ppp secret add name="{peer.user}" password="{peer.password}" service=sstp'
        f" profile={peer.profile} remote-address={peer.address}"
        + (f' routes="{routes}"' if routes else "")
        + f' comment="{tag}"'
    )
    return (
        f"# Olterra - router {peer.router} del ISP {peer.tenant} en el concentrador (SSTP)\n"
        "{\n"
        f'/interface wireguard peers remove [find where comment="{tag}"]\n'
        f'/ppp secret remove [find where comment="{tag}"]\n'
        f'/ppp active remove [find where name="{peer.user}"]\n'
        f"{secret}\n"
        "}\n"
    )


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
        f'/ppp secret remove [find where comment="{tag}"]\n'  # si venía por SSTP
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
    sstp_port: int | None = None,
    sstp_host: str | None = None,
    tunnel_list: str = "olterra-tuneles",
    sstp_profile: str = "olterra-sstp",
) -> str:
    """Configuración inicial del concentrador. Se puede correr de nuevo: rehace lo suyo.

    La llave privada WireGuard la genera el propio RouterOS al crear la interfaz; su
    pública va a ``OLTERRA_TUNNEL_HUB_PUBLIC_KEY``. Con ``sstp_port``, además, el servidor
    SSTP para los routers v6, con su propia CA (la pública va a ``OLTERRA_TUNNEL_SSTP_CA``).

    El aislamiento entre ISP se hace aquí, sobre una lista de interfaces que reúne la de
    WireGuard y cada túnel SSTP (el perfil PPP los agrega solo): la plataforma llega a
    todos, los ISP no se ven entre sí ni entran al concentrador.
    """
    _label(interface, "La interfaz del concentrador")
    _label(tunnel_list, "La lista de interfaces")
    _label(sstp_profile, "El perfil PPP")
    if hub_address not in platform_prefix:
        raise ScriptError("La IP del concentrador va dentro del prefijo de la plataforma")
    if not 1 <= listen_port <= 65535:
        raise ScriptError("Puerto fuera de rango")
    host = None
    if sstp_port is not None:
        if not 1 <= sstp_port <= 65535 or sstp_port == listen_port:
            raise ScriptError("Puerto SSTP fuera de rango")
        if sstp_host is None:
            raise ScriptError(
                "El certificado SSTP necesita la IP o el nombre público del concentrador"
            )
        host = _host(sstp_host)
    tag = "olterra-hub"
    s = _Script()
    s.add("# Olterra - configuracion inicial del concentrador (RouterOS v7)")
    s.add("# Se puede correr de nuevo: rehace solo lo marcado 'olterra-hub'.")
    s.add("{")
    s.add(f':if ([:len [/interface wireguard find where name="{interface}"]] = 0) do={{')
    s.add(
        f'  /interface wireguard add name={interface} listen-port={listen_port} mtu=1420 comment="{tag}"'
    )
    s.add("}")
    s.add(f':if ([:len [/interface list find where name="{tunnel_list}"]] = 0) do={{')
    s.add(f'  /interface list add name={tunnel_list} comment="{tag}"')
    s.add("}")
    s.add(f'/interface list member remove [find where comment="{tag}"]')
    s.add(f'/interface list member add list={tunnel_list} interface={interface} comment="{tag}"')
    s.add(f'/ip address remove [find where comment="{tag}"]')
    s.add(
        f'/ip address add address={hub_address}/{platform_prefix.prefixlen} interface={interface} comment="{tag}"'
    )
    s.add(f'/ip route remove [find where comment="{tag}"]')
    s.add(f'/ip route add dst-address={peer_pool} gateway={interface} comment="{tag}"')
    s.add(f'/ip route add dst-address={nat_pool} gateway={interface} comment="{tag}"')
    if sstp_port is not None and host is not None:
        san = f"IP:{host}" if _is_ip(host) else f"DNS:{host}"
        s.add(f':if ([:len [/ppp profile find where name="{sstp_profile}"]] = 0) do={{')
        s.add(f'  /ppp profile add name={sstp_profile} comment="{tag}"')
        s.add("}")
        s.add(
            f'/ppp profile set [find where name="{sstp_profile}"] local-address={hub_address}'
            f" interface-list={tunnel_list} use-encryption=yes only-one=yes change-tcp-mss=yes"
        )
        s.add(':if ([:len [/certificate find where name="olterra-ca"]] = 0) do={')
        s.add(
            '  /certificate add name=olterra-ca common-name="Olterra CA"'
            " key-usage=key-cert-sign,crl-sign key-size=2048 days-valid=3650"
        )
        s.add("  /certificate sign olterra-ca")
        s.add("}")
        s.add(":local n 0")
        s.add(
            ':while ([:len [/certificate find where name="olterra-ca" and trusted]] = 0 and $n < 30)'
            " do={ :delay 1s; :set n ($n + 1) }"
        )
        s.add(':if ([:len [/certificate find where name="olterra-sstp"]] = 0) do={')
        s.add(
            f'  /certificate add name=olterra-sstp common-name="{host}" subject-alt-name={san}'
            " key-usage=digital-signature,key-encipherment,tls-server key-size=2048 days-valid=3650"
        )
        s.add("  /certificate sign olterra-sstp ca=olterra-ca")
        s.add("}")
        s.add(":set n 0")
        s.add(
            ':while ([:len [/certificate find where name="olterra-sstp" and private-key]] = 0 and $n < 30)'
            " do={ :delay 1s; :set n ($n + 1) }"
        )
        s.add(
            f"/interface sstp-server server set enabled=yes port={sstp_port} certificate=olterra-sstp"
            f" default-profile={sstp_profile} authentication=mschap2 tls-version=only-1.2"
        )
    s.add(f'/ip firewall filter remove [find where comment~"^{tag}"]')
    s.add(":local ffil [/ip firewall filter find where dynamic=no]")
    tunnels = f"in-interface-list={tunnel_list} out-interface-list={tunnel_list}"
    rules = [
        f'chain=input protocol=udp dst-port={listen_port} action=accept comment="{tag}: WireGuard"',
    ]
    if sstp_port is not None:
        rules.append(
            f'chain=input protocol=tcp dst-port={sstp_port} action=accept comment="{tag}: SSTP"'
        )
    rules += [
        # Un ISP del túnel no administra el concentrador: sin esto, si el firewall del CHR solo
        # descarta lo que entra por la WAN, llegaría a Winbox, SSH y la API por la IP del túnel.
        f"chain=input in-interface-list={tunnel_list} src-address=!{platform_prefix} action=drop"
        f' comment="{tag}: los ISP no entran al concentrador"',
        f"chain=forward {tunnels} src-address={platform_prefix}"
        f' action=accept comment="{tag}: plataforma hacia los ISP"',
        f"chain=forward {tunnels} dst-address={platform_prefix}"
        f' protocol=udp dst-port=162 action=accept comment="{tag}: traps hacia la plataforma"',
        f"chain=forward {tunnels}"
        f' connection-state=established,related action=accept comment="{tag}: respuestas"',
        f'chain=forward {tunnels} action=drop comment="{tag}: sin ruteo entre ISP"',
    ]
    for rule in rules:
        s.add_first("/ip firewall filter", "ffil", rule)
    s.add("}")
    return s.text()


def _is_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True
