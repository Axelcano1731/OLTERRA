"""OLT de 8 PON de Tocaima, firmware V1.1.9R, en producción (captura del 2026-10-27).

Salidas reales con nombres de clientes, usuarios PPPoE y seriales reemplazados
(tests/fixtures/vsol-gpon/desconocido-8pon/V1.1.9R/20261027).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall, ParamError
from olterra.drivers.textparse import OnuRef, parse_onu_ref
from olterra.drivers.vsol_gpon import parsers
from olterra.drivers.vsol_gpon.provisioning import parse_onu_running_config

CAPTURE = (
    Path(__file__).resolve().parents[1] / "fixtures/vsol-gpon/desconocido-8pon/V1.1.9R/20261027/cli"
)


def read(name: str) -> str:
    return (CAPTURE / name).read_text(encoding="utf-8")


def test_onu_state_with_rack_slot_pon_index() -> None:
    rows = parsers.parse_onu_state(read("onu.state_pon1.txt"), default_pon=1)
    assert len(rows) == 31
    assert all(row.pon == 1 for row in rows)
    assert {row.phase for row in rows} == {"working", "offline"}
    assert parse_onu_ref("1/1/2:5") == OnuRef(1, 2, 5)


def test_rx_power_zero_means_no_reading() -> None:
    rows = parsers.parse_rx_power(read("onu.rx_power_all_pon1.txt"), default_pon=1)
    assert len(rows) == 31
    no_reading = [row.onu for row in rows if row.rx_dbm is None]
    assert no_reading  # las ONU desconectadas: la OLT pone 0.00
    assert all(row.rx_dbm is None or row.rx_dbm < -8 for row in rows)  # ni una "saturada" falsa


def test_onu_running_config_without_onu_add_line() -> None:
    config = parse_onu_running_config(read("onu.service_config_pon1_onu1.txt"))
    assert (
        config.onu == 1 and config.serial is None
    )  # el serial solo está en la configuración completa
    assert config.template["auth_profile"] == "default"
    assert config.template["services"][0]["vlan"] == 109  # "... vlan 109 cos N/A"
    assert config.client["description"].startswith("CLIENTE_")


def test_pon_statistics_can_be_empty() -> None:
    assert parsers.parse_pon_statistics(read("pon.statistics_pon1.txt")) == {}


def test_optical_info_and_list() -> None:
    optical = parsers.parse_onu_optical(read("onu.optical_pon1_onu1.txt"))
    assert optical.rx_dbm is not None and -30 < optical.rx_dbm < -8
    assert optical.tx_dbm is not None and optical.temperature_c is not None
    assert len(parsers.parse_onu_list(read("onu.list_pon1.txt"), default_pon=1)) == 31


def test_pppoe_change_rewrites_the_saved_line_with_another_user() -> None:
    """Cambiar el PPPoE reescribe la línea que guarda la OLT, con sus mismas opciones."""
    from olterra.drivers.vsol_gpon.parsers import parse_wan_config

    saved = (
        "onu 10 pri wan_conn add route\n"
        "onu 10 pri wan_conn index 1 route tr069_internet bind_lan 1 bind_ssid 1 nat enable"
        " pppoe proxy disable user CLIENTE_001 pwd ****** mode auto\n"
        "onu 10 pri wan_conn index 1 vlan enable vid 109 pri 0\n"
    )
    [wan] = parse_wan_config(saved)
    assert (wan.kind, wan.index, wan.vlan, wan.pppoe_user) == ("wan_conn", 1, 109, "CLIENTE_001")
    steps = get_driver("vsol-gpon").build_cli_steps(
        [
            CommandCall(
                "onu.pppoe_change",
                {
                    "pon": 2,
                    "onu": 10,
                    "wan_kind": wan.kind,
                    "wan": wan.index,
                    "route": wan.route,
                    "tail": wan.tail,
                    "pppoe_user": "CLIENTE_NUEVO",
                },
            )
        ]
    )
    assert [s.command for s in steps][2] == (
        "onu 10 pri wan_conn index 1 route tr069_internet bind_lan 1 bind_ssid 1 nat enable"
        " pppoe proxy disable user CLIENTE_NUEVO pwd {{secret:pppoe_password}} mode auto"
    )


@pytest.mark.parametrize(
    ("route", "tail"),
    [
        ("tr069_internet nat enable\nwrite", "mode auto"),  # otra línea
        ("tr069_internet nat enable", "mode auto"),  # sin "pppoe proxy …"
        ("tr069_internet; reboot pppoe proxy disable", "mode auto"),
        ("ipv4 pppoe proxy disable", "mode auto\nno onu 3"),
    ],
)
def test_pppoe_change_refuses_options_that_are_not_the_olt_s(route: str, tail: str) -> None:
    call = CommandCall(
        "onu.pppoe_change",
        {"pon": 1, "onu": 3, "wan_kind": "wan_conn", "wan": 1, "route": route, "tail": tail,
         "pppoe_user": "X"},
    )  # fmt: skip
    with pytest.raises(ParamError):
        get_driver("vsol-gpon").build_cli_steps([call])


def test_brief_reads_onus_from_ten_on() -> None:
    """Desde la ONU 10 la OLT pega el número al nombre (2026-11-04): antes se perdían el nombre
    y el arriba/abajo de todas esas ONU."""
    text = (
        "GPON0/1:9 ANDRES_FUENTES      up                          GPON-ONUID\n"
        "GPON0/1:10LARRY_ALVAREZ       up                          GPON-ONUID\n"
        "GPON0/1:15MIREYA_MONTOYA      down                        GPON-ONUID\n"
        "GPON0/2:12820_DE_JULIO        up                          GPON-ONUID\n"
        "GPON0/2:3                     up                          GPON-ONUID\n"
    )
    brief = parsers.parse_interfaces_brief(text)
    assert [(o.pon, o.onu, o.description, o.up) for o in brief.onus] == [
        (1, 9, "ANDRES_FUENTES", True),
        (1, 10, "LARRY_ALVAREZ", True),
        (1, 15, "MIREYA_MONTOYA", False),
        (2, 128, "20_DE_JULIO", True),
        (2, 3, None, True),
    ]


def test_tocaima_capture_has_every_onu_with_its_name() -> None:
    brief = parsers.parse_interfaces_brief(
        (CAPTURE / "interfaces.brief.txt").read_text(encoding="utf-8")
    )
    big = [o for o in brief.onus if o.onu >= 10]
    assert big and all(o.description for o in big)
