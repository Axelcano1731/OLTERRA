from __future__ import annotations

import pytest

from olterra.identifiers import clean_text, loose_key, normalize_gpon_serial, normalize_mac


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("VSOL0008D09C", "VSOL0008D09C"),
        ("vsol0008d09c", "VSOL0008D09C"),
        ("VSOL-0008D09C", "VSOL0008D09C"),
        (" VSOL:0008D09C ", "VSOL0008D09C"),
        ("56534F4C0008D09C", "VSOL0008D09C"),  # forma hex de SNMP
        ("0x56534f4c0008d09c", "VSOL0008D09C"),
        ("HWTC1F2E3D4C", "HWTC1F2E3D4C"),
        ("ZTEGC0A1B2C3", "ZTEGC0A1B2C3"),
    ],
)
def test_normalize_gpon_serial(raw: str, expected: str) -> None:
    assert normalize_gpon_serial(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "12345", "VSOL0008D09", "VSOL0008D09CX", "0000000000000000"]
)
def test_normalize_gpon_serial_rejects(raw: str | None) -> None:
    assert normalize_gpon_serial(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "aa:bb:cc:dd:ee:01",
        "AA-BB-CC-DD-EE-01",
        "aabb.ccdd.ee01",
        "AABBCCDDEE01",
        " aa:bb:cc:dd:ee:01 ",
    ],
)
def test_normalize_mac(raw: str) -> None:
    assert normalize_mac(raw) == "AA:BB:CC:DD:EE:01"


@pytest.mark.parametrize("raw", [None, "", "aa:bb:cc", "zz:bb:cc:dd:ee:ff", "cliente"])
def test_normalize_mac_rejects(raw: str | None) -> None:
    assert normalize_mac(raw) is None


def test_loose_key_catches_real_typos() -> None:
    # El caso real de la guía de migración a PPPoE: un espacio antes del "_".
    assert loose_key("DANIELA _PARADA") == loose_key("DANIELA_PARADA")
    assert loose_key("Daniéla_Parada") == loose_key("daniela_parada")
    assert loose_key("cliente​1") == loose_key("cliente1")  # espacio de ancho cero
    assert loose_key("cliente1") != loose_key("cliente2")


def test_clean_text_removes_invisible_chars() -> None:
    assert clean_text("﻿NAP 01 ") == "NAP 01"
