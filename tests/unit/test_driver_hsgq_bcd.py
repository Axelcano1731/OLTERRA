"""Drivers HSGQ-G008 y BCD-1508GS: parsers contra las salidas reales de Tocaima (2026-10-28).

Las salidas están en ``tests/fixtures`` con los nombres de clientes ya reemplazados.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from olterra.drivers import bcd_gpon, get_driver, hsgq_gpon
from olterra.drivers.base import CommandCall, UnrecognizedOutput, UnsupportedCommand

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HSGQ = FIXTURES / "hsgq-gpon" / "HSGQ-G008" / "V1.3.12C" / "20261028" / "cli"
BCD = FIXTURES / "bcd-gpon" / "BCD-1508GS" / "V2.1.6" / "20261028" / "cli"


def _read(folder: Path, name: str) -> str:
    return (folder / f"{name}.txt").read_text(encoding="utf-8")


# --- HSGQ -------------------------------------------------------------------------------------


def test_hsgq_version() -> None:
    info = hsgq_gpon.parse_version(_read(HSGQ, "system.version"))
    assert info.model == "HSGQ-G008"
    assert info.firmware == "HSGQ-G008_IGB_V1.3.12C_Rel"


def test_hsgq_onu_list_shifts_onu_numbers_by_one() -> None:
    rows = hsgq_gpon.parse_onu_list(_read(HSGQ, "onu.list_pon1"), 1)
    assert len(rows) == 11
    first = rows[0]
    # "1/0" en la OLT es la ONU 1 en Olterra (el 0 está reservado para "todo el PON").
    assert (first.pon, first.onu, first.serial) == (1, 1, "GPON21FDE8EC")
    assert first.state == "working"
    assert first.description == "CLIENTE_025"
    inactive = next(r for r in rows if r.serial == "GPON2A2B9560")
    assert inactive.onu == 12
    assert inactive.state == "offline"
    assert sum(r.state == "working" for r in rows) == 10


def test_hsgq_onu_state() -> None:
    states = hsgq_gpon.parse_onu_state(_read(HSGQ, "onu.state_pon1"), 1)
    assert len(states) == 11
    assert all(1 <= s.onu <= 128 for s in states)
    down = next(s for s in states if s.serial == "GPON2A2B9560")
    assert (down.admin_state, down.omcc_state, down.phase) == ("disable", "disable", "offline")


def test_hsgq_interfaces_brief_lists_pons_with_onus() -> None:
    brief = hsgq_gpon.parse_interfaces_brief(_read(HSGQ, "interfaces.brief"))
    assert brief.pons == [1, 2, 3, 5]
    assert len(brief.onus) == 48


def test_hsgq_rx_power() -> None:
    readings = hsgq_gpon.parse_rx_power(_read(HSGQ, "onu.rx_power_all_pon1"), 1)
    assert readings[0].pon == 1
    assert readings[0].onu == 1
    assert readings[0].rx_dbm == pytest.approx(-20.41)
    by_onu = {r.onu: r.rx_dbm for r in readings}
    assert by_onu[16] == pytest.approx(-24.204)  # "1/15" en la OLT


def test_hsgq_autofind_empty() -> None:
    assert hsgq_gpon.parse_autofind(_read(HSGQ, "onu.autofind_pon1"), 1) == []


def test_hsgq_ont_info_without_header_is_unrecognized() -> None:
    with pytest.raises(UnrecognizedOutput):
        hsgq_gpon.parse_onu_list("algo que no es una tabla", 1)


def test_hsgq_runs_queries_in_configure_mode() -> None:
    steps = get_driver("hsgq-gpon").build_cli_steps([CommandCall("onu.state", {"pon": 1})])
    assert [s.command for s in steps][:2] == ["configure", "show ont-info 1 all"]


# --- BCD --------------------------------------------------------------------------------------


def test_bcd_model_and_firmware_come_from_two_commands() -> None:
    assert bcd_gpon.parse_version(_read(BCD, "system.device")).model == "BCD-1508GS"
    firmware = bcd_gpon.parse_version(_read(BCD, "system.version")).firmware
    assert firmware is not None and firmware.startswith("V2.1.6_181108")


def test_bcd_onu_list_joins_state_and_description_tables() -> None:
    rows = bcd_gpon.parse_onu_list(_read(BCD, "onu.list_pon1"), 1)
    assert len(rows) == 21
    first = rows[0]
    assert (first.pon, first.onu, first.serial, first.state) == (1, 1, "GPONB1A801A2", "working")
    assert first.description == "CLIENTE_083"
    assert next(r for r in rows if r.onu == 4).state == "offline"
    assert sum(r.state == "working" for r in rows) == 18


def test_bcd_onu_state() -> None:
    states = bcd_gpon.parse_onu_state(_read(BCD, "onu.state_pon1"), 1)
    assert len(states) == 21
    assert next(s for s in states if s.onu == 4).phase == "offline"


def test_bcd_interfaces_brief() -> None:
    brief = bcd_gpon.parse_interfaces_brief(_read(BCD, "interfaces.brief"))
    assert len(brief.pons) == 7
    assert len(brief.onus) == 116


def test_bcd_rx_power() -> None:
    readings = bcd_gpon.parse_rx_power(_read(BCD, "onu.rx_power_all_pon1"), 1)
    assert len(readings) == 18
    assert (readings[0].pon, readings[0].onu) == (1, 1)
    assert readings[0].rx_dbm == pytest.approx(-22.37)


def test_bcd_autofind_empty_is_not_an_error() -> None:
    text = _read(BCD, "onu.autofind_pon1")
    assert bcd_gpon.parse_autofind(text, 1) == []
    errors = get_driver("bcd-gpon").session.error_patterns
    assert not any(re.search(p, text, re.M) for p in errors)
    assert any(re.search(p, "Error: Parameter error", re.M) for p in errors)


def test_bcd_rx_power_runs_inside_the_gpon_interface() -> None:
    steps = get_driver("bcd-gpon").build_cli_steps([CommandCall("onu.rx_power_all", {"pon": 1})])
    assert [s.command for s in steps][:3] == [
        "config",
        "interface gpon",
        "show ont optical-info 1 all",
    ]


# --- Lo que todavía no existe -----------------------------------------------------------------


@pytest.mark.parametrize("key", ["hsgq-gpon", "bcd-gpon"])
def test_writes_exist_but_are_not_verified(key: str) -> None:
    """El alta salió de la ayuda y la configuración: queda bloqueada hasta probarla."""
    driver = get_driver(key)
    for name in ("onu.authorize", "onu.set_description", "onu.reboot", "onu.delete", "config.save"):
        template = driver.command(name)
        assert template.access.value == "write" and not template.verified, name
    with pytest.raises(UnsupportedCommand):
        driver.command("onu.service_config")  # "copiar una ONU" es solo de VSOL


# --- Lecturas por ONU (capturas del 2026-10-29) -----------------------------------------------

HSGQ_ON = FIXTURES / "hsgq-gpon" / "HSGQ-G008" / "V1.3.12C" / "20261029-onu-en-linea" / "cli"
HSGQ_OFF = FIXTURES / "hsgq-gpon" / "HSGQ-G008" / "V1.3.12C" / "20261029-onu-apagadas" / "cli"
BCD_ON = FIXTURES / "bcd-gpon" / "BCD-1508GS" / "V2.1.6" / "20261029-onu-en-linea" / "cli"
BCD_OFF = FIXTURES / "bcd-gpon" / "BCD-1508GS" / "V2.1.6" / "20261029-onu-apagadas" / "cli"


def test_hsgq_onu_detail_and_version() -> None:
    detail = hsgq_gpon.parse_onu_detail(_read(HSGQ_ON, "onu.detail_pon3_onu9"), {"pon": 3})
    # "ONU ID : 8" en la OLT es la ONU 9 en Olterra.
    assert (detail.pon, detail.onu, detail.phase, detail.distance_m) == (3, 9, "working", 4786)
    assert detail.line_profile == "INTERNET" and detail.name is not None
    assert detail.uptime is not None and detail.uptime.startswith("0 days")
    version = hsgq_gpon.parse_onu_version(_read(HSGQ_ON, "onu.version_pon3_onu9"))
    assert (version.online, version.vendor, version.model, version.software) == (
        True,
        "VSOL",
        "VSOLV512",
        "V3.3.0",
    )
    off = hsgq_gpon.parse_onu_version(_read(HSGQ_OFF, "onu.version_pon1_onu2"))
    assert not off.online and off.model is None
    down = hsgq_gpon.parse_onu_detail(_read(HSGQ_OFF, "onu.detail_pon1_onu12"), {"pon": 1})
    assert (down.onu, down.phase) == (12, "offline")


def test_bcd_onu_detail_version_and_optical() -> None:
    detail = bcd_gpon.parse_onu_detail(_read(BCD_ON, "onu.detail_pon1_onu6"), {"pon": 1})
    assert (detail.pon, detail.onu, detail.phase, detail.distance_m) == (1, 6, "working", 1122)
    assert (detail.line_profile, detail.service_profile) == ("lineprofile_10", "srvprofile_11")
    version = bcd_gpon.parse_onu_version(_read(BCD_ON, "onu.version_pon1_onu6"))
    assert (version.vendor, version.model, version.hardware) == ("VSOL", "VSOLV212", "V2.4")
    optical = bcd_gpon.parse_onu_optical(_read(BCD_ON, "onu.optical_pon1_onu6"), {"pon": 1})
    assert optical is not None
    assert optical.rx_dbm == pytest.approx(-28.862)
    assert optical.bias_ma == pytest.approx(18.7)  # la OLT la da en uA
    # Una ONU apagada: "The ONT does not online", no es un error.
    assert bcd_gpon.parse_onu_optical(_read(BCD_OFF, "onu.optical_pon1_onu4"), {"pon": 1}) is None
    assert not bcd_gpon.parse_onu_version(_read(BCD_OFF, "onu.version_pon1_onu4")).online


def test_hsgq_writes_subtract_one_and_keep_keys_sealed() -> None:
    steps = get_driver("hsgq-gpon").build_cli_steps(
        [
            CommandCall(
                "onu.authorize",
                {"pon": 2, "onu": 5, "serial": "GPON21FDE8EC", "line_profile": 0, "srv_profile": 0},
            ),
            CommandCall("onu.set_description", {"pon": 2, "onu": 5, "description": "Casa_Lopez"}),
            CommandCall(
                "onu.wan_pppoe",
                {
                    "pon": 2,
                    "onu": 5,
                    "vlan": 117,
                    "pppoe_user": "cliente.5",
                    "lan_eth": "1-4",
                    "lan_ssid": "1",
                },
            ),
            CommandCall("onu.wifi", {"pon": 2, "onu": 5, "ssid": "Casa_Lopez"}),
            CommandCall("config.save"),
        ]
    )
    commands = [s.command for s in steps]
    assert commands[:2] == ["configure", "interface gpon 2"]
    assert (
        "ont add 4 sn-auth GPON21FDE8EC omci ont-lineprofile-id 0 ont-srvprofile-id 0" in commands
    )
    assert 'ont setting 4 name "Casa_Lopez"' in commands
    assert (
        "ont wanconfig add 4 connection-type internet vlan tag 117 ipmode pppoe cliente.5"
        " {{secret:pppoe_password}} lan_eth 1-4 lan_ssid 1"
    ) in commands
    assert (
        "ont wificonfig 4 1 wifiname Casa_Lopez security wpa2psk wpaencrypt aes"
        " sharekey {{secret:wifi_key}}"
    ) in commands
    assert commands[-2:] == ["save", "end"] or commands[-1] == "save"


def test_bcd_writes() -> None:
    steps = get_driver("bcd-gpon").build_cli_steps(
        [
            CommandCall(
                "onu.authorize",
                {
                    "pon": 1,
                    "onu": 20,
                    "serial": "GPON0B301264",
                    "line_profile": 10,
                    "srv_profile": 11,
                },
            ),
            CommandCall("onu.set_description", {"pon": 1, "onu": 20, "description": "Casa_Lopez"}),
            CommandCall("config.save"),
        ]
    )
    commands = [s.command for s in steps]
    assert commands[:2] == ["config", "interface gpon"]
    assert (
        "ont add 1 20 sn-auth GPON0B301264 ont-lineprofile-id 10 ont-srvprofile-id 11" in commands
    )
    assert "ont description 1 20 Casa_Lopez" in commands
    assert "save" in commands


@pytest.mark.parametrize(
    ("driver", "model", "pons"),
    [
        (hsgq_gpon, "HSGQ-G008", 8),
        (hsgq_gpon, "HSGQ-G004", 4),
        (hsgq_gpon, None, None),
        (bcd_gpon, "BCD-1508GS", 8),
        (bcd_gpon, "BCD-1504GS", 4),
        (bcd_gpon, "otro", None),
    ],
)
def test_pon_count_from_model(driver: object, model: str | None, pons: int | None) -> None:
    assert driver.pon_count(model) == pons  # type: ignore[attr-defined]
