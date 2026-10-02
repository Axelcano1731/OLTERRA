"""Archivos de la conciliación por tipo, vengan del disco (CLI) o de una subida (API).

Los secretos y las sesiones aceptan CSV o el texto copiado del MikroTik; el resto,
solo CSV. El nombre del archivo decide el formato y da el router por defecto.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath

from olterra.reconciliation.model import CrmCustomer, OnuRecord, PppoeSecret, PppoeSession
from olterra.reconciliation.sources.routeros_text import secrets_from_text, sessions_from_text
from olterra.reconciliation.sources.tabular import (
    customers_from_rows,
    onus_from_rows,
    rows_from_bytes,
    secrets_from_rows,
    sessions_from_rows,
)


@dataclass(frozen=True)
class SourceFile:
    name: str
    data: bytes

    @classmethod
    def read(cls, path: Path) -> SourceFile:
        return cls(path.name, path.read_bytes())

    @property
    def is_tabular(self) -> bool:
        return PurePath(self.name).suffix.lower() in (".csv", ".tsv")

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")

    def rows(self) -> list[dict[str, str]]:
        return rows_from_bytes(self.data, self.name)

    def router_or_stem(self, router: str) -> str:
        return router or PurePath(self.name).stem


def onus_from_files(files: Iterable[SourceFile]) -> list[OnuRecord]:
    result: list[OnuRecord] = []
    for file in files:
        result += onus_from_rows(file.rows(), file.name)
    return result


def secrets_from_files(files: Iterable[SourceFile], router: str = "") -> list[PppoeSecret]:
    result: list[PppoeSecret] = []
    for file in files:
        if file.is_tabular:
            result += secrets_from_rows(file.rows(), file.name)
        else:
            result += secrets_from_text(file.text, file.router_or_stem(router))
    return result


def sessions_from_files(files: Iterable[SourceFile], router: str = "") -> list[PppoeSession]:
    result: list[PppoeSession] = []
    for file in files:
        if file.is_tabular:
            result += sessions_from_rows(file.rows(), file.name)
        else:
            result += sessions_from_text(file.text, file.router_or_stem(router))
    return result


def customers_from_files(files: Iterable[SourceFile]) -> list[CrmCustomer]:
    result: list[CrmCustomer] = []
    for file in files:
        result += customers_from_rows(file.rows(), file.name)
    return result
