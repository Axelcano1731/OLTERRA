"""ONUs desde una captura de ``olterra-capture`` (salidas reales de ``show onu info``)."""

from __future__ import annotations

import json
from pathlib import Path

from olterra.drivers.vsol_gpon.parsers import parse_onu_list
from olterra.reconciliation.model import OnuRecord
from olterra.reconciliation.sources import SourceError


def onus_from_capture(directory: Path, olt_name: str) -> list[OnuRecord]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise SourceError(f"{directory} no tiene manifest.json de olterra-capture")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[OnuRecord] = []
    for entry in manifest["commands"]:
        if entry["key"] != "onu.list" or not entry.get("file") or not entry.get("ok"):
            continue
        text = (directory / entry["file"]).read_text(encoding="utf-8")
        for row in parse_onu_list(text, default_pon=entry.get("pon")):
            records.append(
                OnuRecord.build(
                    olt=olt_name,
                    pon=row.pon,
                    onu=row.onu,
                    serial=row.serial,
                    state=row.state,
                    description=row.description,
                )
            )
    return records
