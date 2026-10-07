"""VSOL V1600G0-B V1.4.8R: lo que la OLT manda de verdad, byte a byte (seriales falsos).

Colocar las columnas con el cursor (``\\r`` + ``ESC[<n>C``) hizo que Olterra perdiera el índice
de la ONU y ningún parser reconociera ``show onu info`` ni ``show onu state`` (2026-10-03).
"""

from __future__ import annotations

from olterra.drivers.vsol_gpon import VSOL_GPON as DRIVER
from olterra.drivers.vsol_gpon import parsers
from olterra.executor.cli import normalize_terminal_text

ESC = "\x1b"

ONU_INFO_RAW = (
    "Onuindex   Model                Profile                Mode    AuthInfo                      \r\n"
    "---------------------------------------------------------------------------------------------\r\n"
    f"GPON0/1:2\r{ESC}[11CV824\r{ESC}[32Cdefault\r{ESC}[55Csn\r{ESC}[63CGPON4F02AA31\r\n\n"
    f"GPON0/1:10\r{ESC}[11Cunknown\r{ESC}[32Cdefault\r{ESC}[55Csn\r{ESC}[63CGPONB4BFD6AA\r\n\n"
)

ONU_STATE_RAW = (
    "OnuIndex    Admin State    OMCC State    Phase State    Serial Number\r\n"
    "---------------------------------------------------------------\r\n"
    f"GPON0/1:2\r{ESC}[12Cenable\r{ESC}[27Cenable\r{ESC}[41Cworking\r{ESC}[56CGPON4F02AA31\r\n"
    f"GPON0/1:9\r{ESC}[12Cenable\r{ESC}[27Cdisable\r{ESC}[41Coffline\r{ESC}[56CGPONB4BFD6AA\r\n"
)


def test_cursor_placed_columns_survive_normalization() -> None:
    text = normalize_terminal_text(ONU_INFO_RAW)
    header, _, first, blank, second = text.splitlines()[:5]
    assert first.split() == ["GPON0/1:2", "V824", "default", "sn", "GPON4F02AA31"]
    assert blank == "" and second.split()[:2] == ["GPON0/1:10", "unknown"]
    # Cada celda en la columna donde la OLT la puso (la misma que su encabezado).
    assert first.index("V824") == header.index("Model")
    assert first.index("GPON4F02AA31") == 63


def test_onu_list_and_state_parse_the_real_output() -> None:
    rows = parsers.parse_onu_list(normalize_terminal_text(ONU_INFO_RAW), 1)
    assert [(r.pon, r.onu, r.model, r.serial) for r in rows] == [
        (1, 2, "V824", "GPON4F02AA31"),
        (1, 10, "unknown", "GPONB4BFD6AA"),
    ]
    states = parsers.parse_onu_state(normalize_terminal_text(ONU_STATE_RAW), 1)
    assert [(s.onu, s.admin_state, s.omcc_state, s.phase, s.serial) for s in states] == [
        (2, "enable", "enable", "working", "GPON4F02AA31"),
        (9, "enable", "disable", "offline", "GPONB4BFD6AA"),
    ]
    assert (
        parsers.parse_onu_state("OnuIndex    Admin State    OMCC State    Phase State\n", 1) == []
    )


def test_rx_power_with_index_column() -> None:
    text = (
        "Onu         ONU_Rx\n------------------------------------\n"
        "2           -18.18\n3           -17.65\n9           N/A\n"
    )
    readings = parsers.parse_rx_power(text, 1)
    assert [(r.onu, r.rx_dbm) for r in readings] == [(2, -18.18), (3, -17.65), (9, None)]


def test_optical_info_distance_description_and_statistics() -> None:
    optical = parsers.parse_onu_optical(
        "Alarm                      : enable\n"
        "Rx optical level(ONU)      : -18.15\n"
        "Lower rx optical threshold : ont internal policy\n"
        "Tx optical level           : 2.28\n"
        "Upper tx optical threshold : ont internal policy\n"
        "Power feed voltage         : 3.26(V)\n"
        "Laser bias current         : 11.386(mA)\n"
        "Temperature                : 55.840(C)\n"
    )
    assert (optical.rx_dbm, optical.tx_dbm) == (-18.15, 2.28)
    assert (optical.voltage_v, optical.bias_ma, optical.temperature_c) == (3.26, 11.386, 55.84)

    assert parsers.parse_onu_distance("onu 2 Distance: 445m\n") == parsers.OnuDistance(2, 445)
    named = parsers.parse_onu_description("onu 2 Description: CLIENTE-PRUEBA\n")
    assert (named.onu, named.description) == (2, "CLIENTE-PRUEBA")
    assert parsers.parse_onu_description("onu 3 Description: \n").description is None

    stats = parsers.parse_pon_statistics(
        "Input rate Bps:      108\nInput rate Pps:      0\nOutput rate Bps:     782\n"
        "Input  bytes:      177983642739\nOutput packets:      1041460181\n"
    )
    assert stats["inputratebps"] == 108 and stats["outputpackets"] == 1_041_460_181


def test_g0b_uses_its_own_syntax_and_others_keep_the_manual() -> None:
    for model in ("V1600G0-B", "v1600g0b"):
        assert DRIVER.command("onu.optical", model, "V1.4.8R").template == (
            "show onu {onu} optical_info"
        )
        assert DRIVER.command("onu.description", model, "V1.4.8R").template == (
            "show onu {onu} desc"
        )
        assert DRIVER.command("pon.statistics", model, "V1.4.8R").template == (
            "show pon {pon} statistics"
        )
        assert DRIVER.command("onu.optical", model, "V1.4.8R").verified
    assert DRIVER.command("onu.optical", "V1600G1B", "V1.4.4R").template == (
        "show onu {onu} optical-info"
    )


def test_autofind_with_sn_prefix_and_tabs() -> None:
    # Tal cual la devolvió la OLT del laboratorio el 2026-10-07 (serial cambiado).
    text = "Index\tSn                    Equipment ID\n1\tsn:GPON00ab12cd\t\tVSOLV422"
    [row] = parsers.parse_autofind(text, 1)
    assert (row.pon, row.serial, row.model, row.index) == (1, "GPON00AB12CD", "VSOLV422", None)
    assert parsers.parse_autofind("", 1) == []
    two = text + "\n2\tsn:VSOL0008d09c\t\tV2802"
    assert [r.serial for r in parsers.parse_autofind(two, 2)] == ["GPON00AB12CD", "VSOL0008D09C"]


def test_profile_already_given_by_onu_add_is_not_sent_again() -> None:
    from olterra.drivers.vsol_gpon.provisioning import ClientData, TemplateBody, authorize_calls

    template = TemplateBody(auth_profile="default", onu_profile="default")  # sin WAN ni WiFi
    client = ClientData(pon=1, onu=1, serial="GPON00AB12CD", description="X")
    keys = [c.key for c in authorize_calls(template, client)]
    assert "onu.bind_onu_profile" not in keys
    other = TemplateBody(auth_profile="default", onu_profile="hgu")
    assert "onu.bind_onu_profile" in [c.key for c in authorize_calls(other, client)]
