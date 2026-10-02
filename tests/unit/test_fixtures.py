"""Las capturas de laboratorio se vuelven pruebas solas.

Cada ``tests/fixtures/<driver>/<modelo>/<firmware>/<fecha>/manifest.json`` (lo deja
``olterra-capture``) se recorre: toda salida con parser tiene que reconocerse. Y un
comando solo puede estar ``verified=True`` si alguna captura lo respalda.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from olterra.drivers import get_driver
from olterra.orchestrator import PARSERS

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
MANIFESTS = sorted(FIXTURES.glob("*/*/*/*/manifest.json"))


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    "manifest",
    MANIFESTS
    or [
        pytest.param(
            None, marks=pytest.mark.skip(reason="Todavía no hay capturas en tests/fixtures")
        )
    ],
    ids=lambda p: "/".join(p.parts[-5:-1]) if isinstance(p, Path) else "sin-capturas",
)
def test_parsers_recognize_lab_captures(manifest: Path) -> None:
    data = _load(manifest)
    checked = 0
    for entry in data["commands"]:
        parser = PARSERS.get(entry["key"])
        if parser is None or not entry["ok"] or not entry.get("file"):
            continue
        text = (manifest.parent / entry["file"]).read_text(encoding="utf-8")
        params = {k: entry[k] for k in ("pon", "onu") if entry.get(k) is not None}
        parser(text, params)  # UnrecognizedOutput = el parser no conoce este firmware
        checked += 1
    assert checked, f"{manifest}: ninguna salida con parser"


def test_verified_commands_have_lab_evidence() -> None:
    evidence = {
        (data["driver"], entry["key"])
        for data in map(_load, MANIFESTS)
        for entry in data["commands"]
        if entry["ok"]
    }
    driver = get_driver("vsol-gpon")
    unbacked = [
        c.key
        for c in driver.commands.values()
        if c.verified and (driver.key, c.key) not in evidence
    ]
    assert not unbacked, f"Marcados como verificados sin captura que los respalde: {unbacked}"


def test_driver_doc_lists_every_command() -> None:
    doc = (ROOT / "docs" / "DRIVERS_VSOL.md").read_text(encoding="utf-8")
    missing = [key for key in get_driver("vsol-gpon").commands if f"`{key}`" not in doc]
    assert not missing, f"Falta documentar en docs/DRIVERS_VSOL.md: {missing}"
