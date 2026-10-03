"""Catálogo de comandos CLI de las OLT VSOL GPON (serie V1600G).

Todos van con ``verified=False``: la sintaxis sale del manual v2.1 y de fuentes
públicas, y cada firmware la cambia un poco (el mismo manual escribe ``uservlan``
en la tabla y ``user-vlan`` en el ejemplo). Un comando pasa a ``verified=True``
cuando hay una captura de laboratorio de ese modelo y firmware en ``tests/fixtures``
y su parser la reconoce.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from olterra.drivers.base import Access, CliMode, CommandOverride, CommandTemplate
from olterra.drivers.vsol_gpon.sources import (
    INFERRED,
    LAB_G0B,
    LIBRENMS_19368,
    PUBLIC_GUIDES,
    manual,
)

R, W = Access.READ, Access.WRITE
EXEC, CONFIG, PON = CliMode.EXEC, CliMode.CONFIG, CliMode.PON


def _c(
    key: str, template: str, mode: CliMode, access: Access, source: str, **kw: Any
) -> CommandTemplate:
    return CommandTemplate(
        key=key, template=template, mode=mode, access=access, source=source, **kw
    )


_COMMANDS = [
    # --- Sistema -------------------------------------------------------------------
    _c("system.version", "show version", CONFIG, R, manual("22.3.2")),
    _c("system.running_time", "show sys running-time", CONFIG, R, manual("22.3.3")),
    _c("system.cpu", "show sys cpu-usage", CONFIG, R, manual("22.3.1")),
    _c("system.memory", "show sys mem", CONFIG, R, manual("22.3.1")),
    _c(
        "system.fan",
        "show fan",
        CONFIG,
        R,
        manual("22.5.8"),
        notes="Incluye la temperatura del equipo",
    ),
    _c("system.running_config", "show running-config", CONFIG, R, manual("22.2.4")),
    _c("system.startup_config", "show startup-config", CONFIG, R, manual("22.2.3")),
    _c("system.alarm_config", "show alarm configuration", CONFIG, R, manual("25.3.1")),
    _c("system.syslog_major", "show syslog level major", CONFIG, R, manual("26.3.1")),
    _c("system.users", "user list", CONFIG, R, manual("23.5")),
    _c("interfaces.brief", "show interface brief", CONFIG, R, PUBLIC_GUIDES),
    _c("snmp.communities", "show snmp-server community", CONFIG, R, manual("24.4.1")),
    _c("snmp.trap_hosts", "show snmp-server targetaddress", CONFIG, R, manual("24.4.2")),
    _c(
        "profile.list",
        "show profile {kind} all",
        CONFIG,
        R,
        manual("20.9"),
        param_types={"kind": "profile_kind"},
    ),
    _c(
        "mac.by_pon",
        "show mac address-table interface gpon 0/{pon}",
        CONFIG,
        R,
        manual("9.3.1"),
        notes="El manual no muestra la sintaxis completa de la interfaz; confirmar",
    ),
    # --- Puerto PON ----------------------------------------------------------------
    _c("pon.info", "show pon info", PON, R, manual("18.3.1")),
    _c("pon.optical", "show pon optical transceiver", PON, R, manual("18.2.2")),
    _c("pon.statistics", "show pon statistics", PON, R, manual("18.2.1")),
    _c("onu.autolearn", "show onu auto-learn", PON, R, manual("17.1")),
    _c("onu.autofind", "show onu auto-find", PON, R, manual("19.2.1")),
    _c("onu.autofind_detail", "show onu auto-find detail-info", PON, R, manual("19.2.1")),
    _c(
        "onu.list",
        "show onu info",
        PON,
        R,
        manual("19.2.3"),
        notes="El manual lo escribe 'Show onuinfo [<1-128>]'",
    ),
    _c("onu.rx_power_all", "show pon onu all rx-power", PON, R, manual("18.2.3")),
    # --- Una ONU -------------------------------------------------------------------
    _c("onu.detail", "show onu detail-info {onu}", PON, R, manual("19.2.4")),
    _c("onu.optical", "show onu {onu} optical-info", PON, R, manual("19.3.1")),
    _c("onu.capability", "show onu {onu} capability", PON, R, manual("19.3.12")),
    _c("onu.service_config", "show running-config onu {onu}", PON, R, manual("19.3.11")),
    _c("onu.description", "show onu {onu} description", PON, R, manual("19.2.7")),
    # Los dos de abajo no están en el manual v2.1: salieron de la ayuda de la V1600G0-B.
    _c("onu.state", "show onu state", PON, R, LAB_G0B, notes="Estado de todas las ONU del PON"),
    _c("onu.distance", "show onu {onu} distance", PON, R, LAB_G0B),
    # --- Escrituras sobre ONU --------------------------------------------------------
    _c(
        "onu.authorize",
        "onu add {onu} profile {profile} sn {serial}",
        PON,
        W,
        manual("19.2.6") + " y ejemplo §19.4.7",
        param_types={"profile": "name"},
    ),
    _c("onu.delete", "no onu {onu}", PON, W, INFERRED),
    _c("onu.reboot", "onu {onu} reboot", PON, W, manual("19.3.4")),
    _c("onu.activate", "onu {onu} activate", PON, W, manual("19.2.5")),
    _c("onu.deactivate", "onu {onu} deactivate", PON, W, manual("19.2.5")),
    _c("onu.set_description", "onu {onu} description {description}", PON, W, manual("19.2.7")),
    _c(
        "onu.bind_line_profile",
        "onu {onu} profile line {profile}",
        PON,
        W,
        manual("20.2"),
        param_types={"profile": "name"},
    ),
    _c(
        "onu.bind_srv_profile",
        "onu {onu} profile srv {profile}",
        PON,
        W,
        manual("20.2"),
        param_types={"profile": "name"},
    ),
    _c(
        "onu.tcont",
        "onu {onu} tcont {tcont} dba {profile}",
        PON,
        W,
        manual("19.3.5"),
        param_types={"profile": "name"},
    ),
    _c("onu.gemport", "onu {onu} gemport {gemport} tcont {tcont}", PON, W, manual("19.3.6")),
    _c(
        "onu.service",
        "onu {onu} service {service} gemport {gemport} vlan {vlan}",
        PON,
        W,
        manual("19.3.7"),
        param_types={"service": "name"},
    ),
    _c(
        "onu.service_port",
        "onu {onu} service-port {service_port} gemport {gemport} uservlan {user_vlan} vlan {vlan}",
        PON,
        W,
        manual("19.3.8"),
        param_types={"user_vlan": "vlan"},
        notes="La tabla del manual dice 'uservlan' y el ejemplo §19.4.7 'user-vlan'",
    ),
    _c(
        "onu.portvlan_tag",
        "onu {onu} portvlan {uni_kind} {uni} mode tag vlan {vlan}",
        PON,
        W,
        manual("19.3.9"),
    ),
    _c(
        "onu.portvlan_transparent",
        "onu {onu} portvlan {uni_kind} {uni} mode transparent",
        PON,
        W,
        manual("19.3.9"),
    ),
    # --- Configuración del equipo ----------------------------------------------------
    _c(
        "config.save",
        "write",
        EXEC,
        W,
        manual("22.2.1"),
        notes="Guarda en flash. VSOL pierde lo no guardado al reiniciar",
    ),
    _c(
        "snmp.set_community",
        "snmp-server community {community} ro",
        CONFIG,
        W,
        manual("24.4.1"),
        sensitive=True,
    ),
    _c(
        "snmp.add_trap_host",
        "snmp-server host {host} version 2c community {community}",
        CONFIG,
        W,
        manual("24.4.2"),
        sensitive=True,
        param_types={"host": "ipv4"},
    ),
    _c("snmp.enable_traps", "snmp-server enable traps snmp", CONFIG, W, manual("24.4.2")),
    _c("snmp.start", "snmp-server start", CONFIG, W, LIBRENMS_19368),
    _c(
        "access.permit",
        "login-access-list permit {service} {host} {mask}",
        CONFIG,
        W,
        LIBRENMS_19368 + "; " + PUBLIC_GUIDES,
        param_types={"service": "access_service", "host": "ipv4", "mask": "netmask"},
    ),
    _c(
        "user.add",
        "user add {username} login-password {password}",
        CONFIG,
        W,
        manual("23.4"),
        sensitive=True,
    ),
    _c(
        "user.role_admin",
        "user role {username} admin",
        CONFIG,
        W,
        manual("23.4"),
        notes="La sintaxis del manual es ambigua: '{admin | normal enable-password …}'",
    ),
    _c("user.delete", "user delete {username}", CONFIG, W, manual("23.6")),
    # Cambio de claves de acceso. La clave nueva va por ``secret_params``: el plan lleva el
    # marcador y el ejecutor la toma de la credencial sellada. SIN verificar: la ayuda de la
    # V1600G0-B (`user ?`) lista ``login-password`` y ``enable-password``, pero falta confirmar en
    # el laboratorio si la clave se escribe en la misma línea o la pide aparte.
    _c(
        "user.set_login_password",
        "user login-password {username} {password}",
        CONFIG,
        W,
        LAB_G0B,
        sensitive=True,
        secret_params={"password": "new_password"},
        notes="Clave de acceso (SSH/web) de un usuario. Sintaxis por confirmar en laboratorio",
    ),
    _c(
        "user.set_enable_password",
        "user enable-password {username} {password}",
        CONFIG,
        W,
        LAB_G0B,
        sensitive=True,
        secret_params={"password": "new_enable_password"},
        notes="Clave de enable de un usuario. Sintaxis por confirmar en laboratorio",
    ),
]

# Comandos que respondieron en la captura de la V1600G0-B V1.4.8R (tests/fixtures). Con la
# sintaxis del catálogo: los que ahí fallaron (profile.list, pon.statistics, onu.optical,
# onu.description) no están aquí; su sintaxis de ese modelo va como override, abajo.
_LAB_VERIFIED = {
    "system.version", "system.running_time", "system.cpu", "system.memory", "system.fan",
    "system.running_config", "system.startup_config", "system.alarm_config",
    "system.syslog_major", "system.users", "interfaces.brief", "snmp.communities",
    "snmp.trap_hosts", "mac.by_pon", "pon.info", "pon.optical", "onu.autolearn",
    "onu.autofind", "onu.autofind_detail", "onu.list", "onu.rx_power_all", "onu.detail",
    "onu.capability", "onu.service_config", "onu.state", "onu.distance",
}  # fmt: skip

COMMANDS: dict[str, CommandTemplate] = {
    command.key: dataclasses.replace(command, verified=True)
    if command.key in _LAB_VERIFIED
    else command
    for command in _COMMANDS
}

OVERRIDES = [
    # V1600G0-B V1.4.8R: el manual v2.1 escribe otras palabras (la ayuda '?' de la OLT dice
    # optical_info, desc y 'show pon <n> statistics').
    CommandOverride(
        "V1600G0*", "*", "onu.optical", "show onu {onu} optical_info", LAB_G0B, verified=True
    ),
    CommandOverride(
        "V1600G0*", "*", "onu.description", "show onu {onu} desc", LAB_G0B, verified=True
    ),
    CommandOverride(
        "V1600G0*", "*", "pon.statistics", "show pon {pon} statistics", LAB_G0B, verified=True
    ),
    CommandOverride("V1600GS*", "*", "config.save", "write memory", LIBRENMS_19368),
    CommandOverride(
        "V1600GS*",
        "*",
        "snmp.add_trap_host",
        "snmp-server trap-host {host} community {community}",
        LIBRENMS_19368,
    ),
]
