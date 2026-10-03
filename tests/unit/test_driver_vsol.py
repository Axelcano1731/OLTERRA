"""Driver VSOL GPON.

OJO: las salidas de CLI de este archivo son SINTÉTICAS (formatos plausibles, no
capturas). Cuando lleguen las del laboratorio, van en ``tests/fixtures`` y las
prueba ``test_fixtures.py``; estas se quedan como red mínima.
"""

from __future__ import annotations

import pytest

from olterra.drivers import get_driver
from olterra.drivers.base import (
    Access,
    Capability,
    CommandCall,
    ParamError,
    Support,
    UnrecognizedOutput,
)
from olterra.drivers.vsol_gpon import parsers
from olterra.drivers.vsol_gpon import snmp as vsnmp
from olterra.executor.plan import Varbind

DRIVER = get_driver("vsol-gpon")


def commands(calls: list[CommandCall], **kw: str) -> list[str]:
    return [step.command for step in DRIVER.build_cli_steps(calls, **kw)]


# --- Plantillas y modos -------------------------------------------------------------


def test_mode_transitions_group_by_pon_and_end_in_exec() -> None:
    result = commands(
        [
            CommandCall("onu.list", {"pon": 1}),
            CommandCall("onu.optical", {"pon": 1, "onu": 5}),
            CommandCall("onu.list", {"pon": 2}),
            CommandCall("system.version"),
            CommandCall("config.save"),
        ]
    )
    assert result == [
        "configure terminal",
        "interface gpon 0/1",
        "show onu info",
        "show onu 5 optical-info",
        "exit",
        "interface gpon 0/2",
        "show onu info",
        "exit",
        "show version",
        "end",
        "write",
    ]


def test_exec_only_plan_has_no_transitions() -> None:
    assert commands([CommandCall("config.save")]) == ["write"]


def test_authorize_renders_with_validated_params() -> None:
    result = commands(
        [
            CommandCall(
                "onu.authorize", {"pon": 1, "onu": 3, "profile": "HGU", "serial": "VSOL0008D09C"}
            )
        ]
    )
    assert "onu add 3 profile HGU sn VSOL0008D09C" in result


@pytest.mark.parametrize(
    ("key", "params"),
    [
        ("onu.set_description", {"pon": 1, "onu": 1, "description": "cliente\nno onu 1"}),
        ("onu.set_description", {"pon": 1, "onu": 1, "description": "con espacio"}),
        (
            "onu.authorize",
            {"pon": 1, "onu": 3, "profile": "HGU", "serial": "VSOL0008D09C; no onu 1"},
        ),
        ("onu.authorize", {"pon": 1, "onu": 129, "profile": "HGU", "serial": "VSOL0008D09C"}),
        ("onu.reboot", {"pon": 17, "onu": 1}),
        ("onu.reboot", {"pon": 1, "onu": True}),
        ("snmp.add_trap_host", {"host": "198.18.0.2 extra", "community": "comunidad123"}),
        ("user.add", {"username": "noc", "password": "con espacio"}),
    ],
)
def test_injection_and_out_of_range_params_are_rejected(
    key: str, params: dict[str, object]
) -> None:
    with pytest.raises(ParamError):
        DRIVER.build_cli_steps([CommandCall(key, params)])


def test_missing_param_is_rejected() -> None:
    with pytest.raises(ParamError, match="falta"):
        DRIVER.build_cli_steps([CommandCall("onu.optical", {"pon": 1})])


def test_overrides_by_model() -> None:
    assert commands([CommandCall("config.save")], model="V1600GS") == ["write memory"]
    assert commands([CommandCall("config.save")], model="V1600G1B") == ["write"]
    gs = DRIVER.command("snmp.add_trap_host", "V1600GS", "V4.0.0")
    assert gs.render(host="198.18.0.2", community="comunidad123") == (
        "snmp-server trap-host 198.18.0.2 community comunidad123"
    )
    assert gs.sensitive


def test_every_command_documents_its_source() -> None:
    # Que esté verificado o no lo controla test_fixtures.py (exige una captura que lo respalde).
    for command in DRIVER.commands.values():
        assert command.source, command.key


def test_read_only_catalog_has_no_writes() -> None:
    assert all(c.access is Access.READ for c in DRIVER.read_only_catalog())
    assert all(
        not c.template.startswith(("onu add", "no ", "write")) for c in DRIVER.read_only_catalog()
    )


# --- Capacidades ----------------------------------------------------------------------


def test_capabilities_per_model_and_firmware() -> None:
    gs = DRIVER.capabilities("V1600GS", "V4.0.0")
    assert gs[Capability.SNMP_ONU_OPTICAL].support is Support.YES
    assert "19368" in gs[Capability.SNMP_ONU_OPTICAL].source

    g1b_old = DRIVER.capabilities("V1600G1B", "V1.4.4R")
    assert g1b_old[Capability.SNMP_ONU_OPTICAL].support is Support.NO
    assert g1b_old[Capability.CLI_ONU_OPTICAL].support is Support.YES  # el plan B

    g1b_new = DRIVER.capabilities("V1600G1B", "V2.0.0")
    assert g1b_new[Capability.SNMP_ONU_OPTICAL].support is Support.UNKNOWN

    unknown = DRIVER.capabilities(None, None)
    assert unknown[Capability.ONU_WIFI_VIA_OLT].support is Support.UNKNOWN
    assert unknown[Capability.CLI_AUTOFIND].support is Support.YES


def test_model_matches_with_or_without_dash() -> None:
    # Como sale en la web de la OLT ("Device Model": V1600G1-B) o como lo escriben otros.
    for model in ("V1600G1-B", "v1600g1b", "V1600G1 B"):
        facts = DRIVER.capabilities(model, "V1.4.4R")
        assert facts[Capability.SNMP_ONU_OPTICAL].support is Support.NO, model
    assert commands([CommandCall("config.save")], model="v1600gs") == ["write memory"]
    # Un modelo sin reglas propias (V1600G0-B) recibe las generales de VSOL GPON.
    g0b = DRIVER.capabilities("V1600G0-B", "V1.4.8R")
    assert g0b[Capability.CLI_ONU_OPTICAL].support is Support.YES
    assert g0b[Capability.SNMP_ONU_OPTICAL].support is Support.UNKNOWN


# --- Parsers CLI (salidas sintéticas) -----------------------------------------------------

ONU_INFO = """
Onuindex    Model       Profile   Mode  AuthInfo        State
----------------------------------------------------------------------
GPON0/1:1   V2802DAC    HGU       sn    VSOL0008D09C    working
GPON0/1:2   HG8310M     SFU       sn    hwtc-1f2e3d4c   offline
"""


def test_parse_onu_list() -> None:
    rows = parsers.parse_onu_list(ONU_INFO, default_pon=1)
    assert [(r.pon, r.onu, r.serial, r.state) for r in rows] == [
        (1, 1, "VSOL0008D09C", "working"),
        (1, 2, "HWTC1F2E3D4C", "offline"),
    ]
    assert rows[0].model == "V2802DAC"
    assert rows[0].profile == "HGU"


def test_parse_onu_list_finds_serial_column_by_content() -> None:
    text = (
        "Id  Equipo      Codigo          Estado\n"
        + "-" * 40
        + "\n3   V2802DAC    VSOL00A1B2C3    online\n"
    )
    [row] = parsers.parse_onu_list(text, default_pon=2)
    assert (row.pon, row.onu, row.serial, row.state) == (2, 3, "VSOL00A1B2C3", "online")


def test_parse_onu_list_empty_and_unrecognized() -> None:
    assert parsers.parse_onu_list("") == []
    assert parsers.parse_onu_list("No onu found on this port") == []
    assert parsers.parse_onu_list("Onuindex  Model  State\n-------------------------\n") == []
    with pytest.raises(UnrecognizedOutput):
        parsers.parse_onu_list("Respuesta en un formato que nadie esperaba")
    with pytest.raises(UnrecognizedOutput):
        parsers.parse_onu_list("Nombre  Valor\n-------------\nfoo     bar\n")


def test_parse_autofind() -> None:
    text = (
        "Onuindex    Sn              State\n" + "-" * 40 + "\nGPON0/2:1   VSOL00BEEF01    unknown\n"
    )
    [row] = parsers.parse_autofind(text)
    assert (row.pon, row.index, row.serial) == (2, 1, "VSOL00BEEF01")


def test_parse_autofind_fiberhome_style_columns() -> None:
    # Formato del tutorial de FiberHome que circula entre técnicos (columna Port aparte).
    text = (
        "Index Port SN Password Autofind-Time\n"
        "------------------------------------------------------\n"
        "1 7 VSOL0008D09C 1234567890 2000-04-01 16:38:37\n"
    )
    [row] = parsers.parse_autofind(text)
    assert row.serial == "VSOL0008D09C"
    assert row.pon == 7


def test_parse_rx_power() -> None:
    text = "Onuindex    Rx Power(dBm)\n" + "-" * 30 + "\nGPON0/1:1   -19.50\nGPON0/1:3   N/A\n"
    rows = parsers.parse_rx_power(text)
    assert [(r.pon, r.onu, r.rx_dbm) for r in rows] == [(1, 1, -19.5), (1, 3, None)]


def test_parse_onu_optical() -> None:
    text = (
        "Rx optical power(dBm)  : -19.50\n"
        "Tx optical power(dBm)  : 2.15\n"
        "Temperature(C)         : 41.00\n"
        "Voltage(V)             : 3.28\n"
        "Bias current(mA)       : 13.20\n"
    )
    info = parsers.parse_onu_optical(text)
    assert (info.rx_dbm, info.tx_dbm, info.temperature_c, info.voltage_v, info.bias_ma) == (
        -19.5,
        2.15,
        41.0,
        3.28,
        13.2,
    )
    with pytest.raises(UnrecognizedOutput):
        parsers.parse_onu_optical("Error: onu is not exist")


def test_parse_version() -> None:
    version = parsers.parse_version(
        "Device Type          : V1600G1B\nHardware Version     : V2.0\nSoftware Version     : V1.4.4R\n"
    )
    assert (version.model, version.firmware, version.hardware) == ("V1600G1B", "V1.4.4R", "V2.0")
    assert parsers.parse_version("V1600GS booted ok").model == "V1600GS"
    # V1600G0-B V1.4.8R: el número de serie también empieza con V; no es el modelo.
    real = parsers.parse_version(
        "  Olt Serial Number:           V2504240405\n  Olt Device Model:            V1600G0-B\n"
        "  Hardware Version:            V3.1.4\n  Software Version:            V1.4.8R\n"
    )
    assert (real.model, real.firmware, real.hardware) == ("V1600G0-B", "V1.4.8R", "V3.1.4")
    with pytest.raises(UnrecognizedOutput):
        parsers.parse_version("nada útil")


# --- SNMP (OIDs del MIB V1600G; valores con el formato del snmprec de un V1600GS) ---------


def vb(oid: str, value: str, type_: str = "OctetString") -> Varbind:
    return Varbind(oid=oid, type=type_, value=value)


def test_snmp_status_auth_optical_and_distance() -> None:
    root = vsnmp.ROOT
    varbinds = [
        vb(f"{root}.1.1.3.1.1", "1", "Integer"),
        vb(f"{root}.1.1.5.1.1", "3", "Integer"),
        vb(f"{root}.1.1.5.1.2", "1", "Integer"),
        vb(f"{root}.1.1.7.1.1", "cliente-1"),
        vb(f"{root}.2.1.3.1.1", "HGU"),
        vb(f"{root}.2.1.4.1.1", "1", "Integer"),
        vb(f"{root}.2.1.5.1.1", "0x56534f4c0008d09c"),
        vb(f"{root}.2.1.6.1.1", "V2802DAC"),
        vb(f"{root}.3.1.3.1.1", "40.00"),
        vb(f"{root}.3.1.6.1.1", "2.14"),
        vb(f"{root}.3.1.7.1.1", "-10.47"),
        vb(f"{root}.3.1.8.1.1", "0.00"),
        vb(f"{root}.12.1.3.1.1", "1834", "Integer"),
        vb(f"{root}.99.1.3.1.1", "ignorar"),  # otra tabla
        vb(f"{root}.3.1.7.1", "fila mal formada"),
    ]
    [status_1, status_2] = vsnmp.parse_status(varbinds)
    assert status_1.online and status_1.admin_enabled and status_1.description == "cliente-1"
    assert status_2.phase == "los" and not status_2.online
    [auth] = vsnmp.parse_auth(varbinds)
    assert (auth.serial, auth.auth_mode, auth.model, auth.profile) == (
        "VSOL0008D09C",
        "sn",
        "V2802DAC",
        "HGU",
    )
    [optical] = vsnmp.parse_optical(varbinds)
    assert (optical.rx_dbm, optical.tx_dbm, optical.temperature_c) == (-10.47, 2.14, 40.0)
    assert optical.olt_rx_dbm is None  # 0.00 = sin lectura
    assert vsnmp.parse_distance(varbinds) == {(1, 1): 1834}


def test_detect_model_from_sysdescr() -> None:
    assert vsnmp.detect_model("V1600G1B") == "V1600G1B"
    assert vsnmp.detect_model("V-SOL V1600GS GPON OLT") == "V1600GS"
    assert vsnmp.detect_model("Linux") is None
