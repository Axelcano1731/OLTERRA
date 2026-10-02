"""Parsers genéricos y tolerantes para salidas de CLI de equipos de red.

No suponen un formato exacto: buscan tablas (encabezado + línea de guiones),
pares ``clave : valor`` y referencias tipo ``GPON0/1:5``. Los parsers de cada
driver se arman encima y deciden qué columnas son obligatorias.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEPARATOR = re.compile(r"^[\s\-=+|]*[-=]{5,}[\s\-=+|]*$")
_ONU_REF = re.compile(
    r"^(?:(?:x?gpon|epon|pon)[\s_-]*)?(?:(\d+)\s*/\s*)?(\d+)\s*[:/]\s*(\d+)$", re.IGNORECASE
)
_FLOAT = re.compile(r"[-+]?\d+(?:\.\d+)?")
_DBM_IN_PARENS = re.compile(r"\(\s*([-+]?\d+(?:\.\d+)?)\s*dBm\s*\)", re.IGNORECASE)
_DBM = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*dBm", re.IGNORECASE)


def norm_header(name: str) -> str:
    """``"ONU ID"`` → ``"onuid"``; ``"Auth-Info"`` → ``"authinfo"``."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class Table:
    headers: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def normalized(self) -> list[str]:
        return [norm_header(h) for h in self.headers]

    def column(self, *candidates: str) -> str | None:
        """Primer encabezado cuyo nombre normalizado está en ``candidates``."""
        normalized = self.normalized
        for candidate in candidates:
            if candidate in normalized:
                return self.headers[normalized.index(candidate)]
        return None


def _header_columns(header: str) -> tuple[list[tuple[str, int]], bool]:
    """Columnas del encabezado con su posición de inicio, y si vienen separadas por un solo espacio.

    Primero se separa por 2+ espacios (encabezados de varias palabras); si eso da una
    sola columna, por espacios simples.
    """
    columns = [(m.group(0), m.start()) for m in re.finditer(r"\S+(?: \S+)*", header)]
    if len(columns) > 1:
        return columns, False
    return [(m.group(0), m.start()) for m in re.finditer(r"\S+", header)], True


def _split_row(line: str, columns: list[tuple[str, int]], single_spaced: bool) -> list[str]:
    tokens = line.split()
    if len(tokens) == len(columns):
        return tokens
    if single_spaced and len(tokens) > len(columns):
        # Sin alineación por columnas, lo que sobra es de la última (p. ej. "fecha hora").
        keep = len(columns) - 1
        return [*tokens[:keep], " ".join(tokens[keep:])]
    cells = []
    for i, (_, start) in enumerate(columns):
        end = columns[i + 1][1] if i + 1 < len(columns) else None
        cells.append(line[start:end].strip() if start < len(line) else "")
    return cells


def find_tables(text: str) -> list[Table]:
    lines = text.splitlines()
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        if not _SEPARATOR.match(lines[i]):
            i += 1
            continue
        header_index = i - 1
        while header_index >= 0 and not lines[header_index].strip():
            header_index -= 1
        if header_index < 0 or _SEPARATOR.match(lines[header_index]):
            i += 1
            continue
        columns, single_spaced = _header_columns(lines[header_index].rstrip())
        table = Table(headers=[name for name, _ in columns])
        i += 1
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                # Un blanco termina la tabla salvo que siga otra fila de datos.
                if (
                    i + 1 < len(lines)
                    and lines[i + 1].strip()
                    and not _SEPARATOR.match(lines[i + 1])
                ):
                    nxt = lines[i + 1].split()
                    if len(nxt) == len(columns):
                        i += 1
                        continue
                break
            if _SEPARATOR.match(line):
                # Separador de pie (p. ej. antes de un "Total"): fin de la tabla.
                i += 1
                break
            cells = _split_row(line.rstrip(), columns, single_spaced)
            table.rows.append(dict(zip(table.headers, cells, strict=False)))
            i += 1
        if table.rows:
            tables.append(table)
    return tables


def parse_key_values(text: str) -> dict[str, str]:
    """Pares ``clave : valor`` (también ``clave = valor``) con la clave normalizada."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z][\w ()./%\-]*?)\s*[:=]\s*(.*?)\s*$", line)
        if match and match.group(2):
            result.setdefault(norm_header(match.group(1)), match.group(2))
    return result


@dataclass(frozen=True)
class OnuRef:
    slot: int | None
    pon: int | None
    onu: int


def parse_onu_ref(text: str, default_pon: int | None = None) -> OnuRef | None:
    """``GPON0/1:5`` → (0, 1, 5); ``1:5`` → (None, 1, 5); ``5`` → (None, default_pon, 5)."""
    value = text.strip()
    match = _ONU_REF.match(value)
    if match:
        slot, pon, onu = match.groups()
        return OnuRef(int(slot) if slot is not None else None, int(pon), int(onu))
    if value.isdigit():
        return OnuRef(None, default_pon, int(value))
    return None


def parse_dbm(text: str | None) -> float | None:
    """``-21.5``, ``-21.50 dBm``, ``0.00 mW (-23.19 dBm)`` → float. ``N/A``/``--`` → None."""
    if text is None:
        return None
    value = text.strip()
    if not value or value.upper() in {"N/A", "NA", "--", "-", "NULL", "INVALID"}:
        return None
    for pattern in (_DBM_IN_PARENS, _DBM):
        match = pattern.search(value)
        if match:
            return float(match.group(1))
    match = _FLOAT.search(value)
    if match and "mw" not in value.lower():
        return float(match.group(0))
    return None


def parse_number(text: str | None) -> float | None:
    if text is None:
        return None
    match = _FLOAT.search(text)
    return float(match.group(0)) if match else None
