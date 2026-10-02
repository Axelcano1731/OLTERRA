from __future__ import annotations

import pytest

from olterra.drivers.textparse import (
    OnuRef,
    find_tables,
    parse_dbm,
    parse_key_values,
    parse_onu_ref,
)


def test_find_tables_with_multiword_headers_and_values() -> None:
    text = """
Onuindex   Model        Last Online Time      Status
------------------------------------------------------
GPON0/1:1  V2802DAC     2026-09-30 10:00:01   online
GPON0/1:2  HG8310M      2026-09-29 08:12:44   offline
"""
    [table] = find_tables(text)
    assert table.headers == ["Onuindex", "Model", "Last Online Time", "Status"]
    assert table.rows[1]["Last Online Time"] == "2026-09-29 08:12:44"
    assert table.rows[1]["Status"] == "offline"
    assert table.column("status", "state") == "Status"


def test_find_tables_stops_at_footer_and_handles_two_tables() -> None:
    text = """
Port  State
----------
1     up
2     down
----------
Total: 2

ID  SN
------
1   VSOL0008D09C
"""
    tables = find_tables(text)
    assert [len(t.rows) for t in tables] == [2, 1]
    assert tables[1].rows[0]["SN"] == "VSOL0008D09C"


def test_find_tables_ignores_header_without_rows() -> None:
    assert find_tables("Onuindex  Sn  State\n-------------------\n") == []


def test_parse_key_values_normalizes_keys() -> None:
    kv = parse_key_values(
        "Rx optical power(dBm)  : -19.50\nSoftware Version = V1.4.4R\nsin valor :\n"
    )
    assert kv["rxopticalpowerdbm"] == "-19.50"
    assert kv["softwareversion"] == "V1.4.4R"
    assert "sinvalor" not in kv


@pytest.mark.parametrize(
    ("text", "default_pon", "expected"),
    [
        ("GPON0/1:5", None, OnuRef(0, 1, 5)),
        ("gpon 0/12:128", None, OnuRef(0, 12, 128)),
        ("1:7", None, OnuRef(None, 1, 7)),
        ("7", 3, OnuRef(None, 3, 7)),
        ("EPON0/2:3", None, OnuRef(0, 2, 3)),
    ],
)
def test_parse_onu_ref(text: str, default_pon: int | None, expected: OnuRef) -> None:
    assert parse_onu_ref(text, default_pon) == expected


def test_parse_onu_ref_rejects_text() -> None:
    assert parse_onu_ref("Total") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-21.5", -21.5),
        ("-21.50 dBm", -21.5),
        ("0.00 mW (-23.19 dBm)", -23.19),  # formato de VSOL V1600D (LibreNMS PR #19845)
        ("2.15", 2.15),
        ("N/A", None),
        ("--", None),
        ("", None),
        (None, None),
        ("0.01 mW", None),  # solo mW: no se adivina la conversión
    ],
)
def test_parse_dbm(text: str | None, expected: float | None) -> None:
    assert parse_dbm(text) == expected
