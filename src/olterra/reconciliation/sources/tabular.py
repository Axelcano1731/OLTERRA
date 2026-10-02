"""CSV exportados de Excel, de WispHub, de Mikrowisp o de ISPWatch.

Acepta ``,`` o ``;`` (Excel en español usa ``;``), UTF-8 o Windows-1252, y
encabezados en español o inglés, con o sin tildes. Si falta una columna
obligatoria, el error dice cuáles nombres se aceptan.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from olterra.drivers.textparse import parse_onu_ref
from olterra.reconciliation.model import (
    CrmCustomer,
    OnuRecord,
    PppoeSecret,
    PppoeSession,
    crm_serial,
    normalize_access,
    normalize_status,
)
from olterra.reconciliation.sources import SourceError

_TRUE = {"si", "sí", "yes", "true", "1", "x", "deshabilitado", "disabled"}


def norm_column(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def read_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - cp1252 decodifica casi todo
        raise SourceError(f"{path.name}: codificación desconocida")
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise SourceError(f"{path.name}: el archivo no tiene encabezados")
    rows = []
    for row in reader:
        rows.append({norm_column(k): (v or "") for k, v in row.items() if k is not None})
    return rows


class _Columns:
    def __init__(self, rows: list[dict[str, str]], source: str) -> None:
        self.available = set(rows[0]) if rows else set()
        self.source = source

    def find(self, *aliases: str) -> str | None:
        for alias in aliases:
            if alias in self.available:
                return alias
        return None

    def require(self, what: str, *aliases: str) -> str:
        column = self.find(*aliases)
        if column is None:
            raise SourceError(
                f"{self.source}: falta la columna de {what}. Se acepta cualquiera de: {', '.join(aliases)}"
            )
        return column


def _get(row: Mapping[str, str], column: str | None) -> str | None:
    if column is None:
        return None
    value = row.get(column, "")
    return value if value.strip() else None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def onus_from_rows(rows: list[dict[str, str]], source: str = "onus.csv") -> list[OnuRecord]:
    if not rows:
        return []
    cols = _Columns(rows, source)
    serial = cols.require("serial", "serial", "sn", "serialonu", "onuserial", "serialnumber")
    olt = cols.find("olt", "nombreolt", "equipo")
    pon = cols.find("pon", "puertopon", "ponport", "puerto")
    onu = cols.find("onu", "onuid", "onuindex", "indice", "id")
    state = cols.find("estado", "state", "status", "fase")
    description = cols.find("descripcion", "description", "desc", "nombre")
    pppoe = cols.find("pppoe", "usuariopppoe", "pppoeuser", "wanpppoe")
    macs = cols.find("macs", "mac", "macaddress")
    result = []
    for row in rows:
        ref = parse_onu_ref(_get(row, onu) or "", _int(_get(row, pon)))
        result.append(
            OnuRecord.build(
                olt=_get(row, olt) or "OLT",
                pon=ref.pon if ref else _int(_get(row, pon)),
                onu=ref.onu if ref else None,
                serial=_get(row, serial),
                state=_get(row, state),
                description=_get(row, description),
                pppoe_user=_get(row, pppoe),
                macs=re.split(r"[\s;,]+", _get(row, macs) or "") if macs else (),
            )
        )
    return result


def secrets_from_rows(
    rows: list[dict[str, str]], source: str = "secretos.csv"
) -> list[PppoeSecret]:
    if not rows:
        return []
    cols = _Columns(rows, source)
    name = cols.require(
        "usuario PPPoE", "usuario", "name", "user", "pppoe", "usuariopppoe", "secret"
    )
    router = cols.find("router", "mikrotik", "rb", "core")
    profile = cols.find("perfil", "profile", "plan")
    disabled = cols.find("deshabilitado", "disabled", "inactivo")
    comment = cols.find("comentario", "comment")
    return [
        PppoeSecret(
            router=_get(row, router) or "MikroTik",
            name=row[name],
            profile=_get(row, profile),
            disabled=(_get(row, disabled) or "").strip().lower() in _TRUE,
            comment=_get(row, comment),
        )
        for row in rows
        if row.get(name, "")
    ]


def sessions_from_rows(
    rows: list[dict[str, str]], source: str = "sesiones.csv"
) -> list[PppoeSession]:
    if not rows:
        return []
    cols = _Columns(rows, source)
    name = cols.require("usuario PPPoE", "usuario", "name", "user", "pppoe")
    router = cols.find("router", "mikrotik", "rb", "core")
    caller = cols.find("callerid", "mac", "macaddress")
    address = cols.find("ip", "address", "direccion")
    return [
        PppoeSession(
            router=_get(row, router) or "MikroTik",
            name=row[name],
            caller_id=_get(row, caller),
            address=_get(row, address),
        )
        for row in rows
        if row.get(name, "")
    ]


def customers_from_rows(
    rows: list[dict[str, str]], source: str = "clientes.csv"
) -> list[CrmCustomer]:
    if not rows:
        return []
    cols = _Columns(rows, source)
    cid = cols.find("id", "idcliente", "customerid", "codigo", "cedula", "documento")
    name = cols.find("nombre", "name", "cliente", "customer")
    last_name = cols.find("apellido", "apellidos", "lastname")
    status = cols.find("estado", "status", "servicestatus", "estadoservicio")
    pppoe = cols.find("pppoe", "usuariopppoe", "pppoeusername", "usuario")
    serial = cols.find("serialonu", "serial", "sn", "onuserial", "onu")
    access = cols.find("acceso", "access", "tecnologia", "isfiber", "fibra", "tipo")
    nap = cols.find("nap", "cajanap", "sectorial", "caja")
    nap_port = cols.find("puertonap", "napport", "puerto")
    router = cols.find("router", "mikrotik", "core")
    if pppoe is None and serial is None:
        raise SourceError(
            f"{source}: hace falta al menos la columna del usuario PPPoE o la del serial de la ONU"
        )
    result = []
    for number, row in enumerate(rows, start=2):  # fila 1 = encabezados
        full_name = " ".join(x for x in (_get(row, name), _get(row, last_name)) if x)
        result.append(
            CrmCustomer(
                id=_get(row, cid) or f"fila-{number}",
                name=full_name or None,
                status=normalize_status(_get(row, status)),
                pppoe_user=_get(row, pppoe),
                onu_serial=crm_serial(_get(row, serial)),
                access=normalize_access(_get(row, access)),
                nap=_get(row, nap),
                nap_port=_get(row, nap_port),
                router=_get(row, router),
            )
        )
    return result


TEMPLATES: dict[str, list[str]] = {
    "onus.csv": ["olt", "pon", "onu", "serial", "estado", "descripcion", "pppoe", "macs"],
    "secretos.csv": ["router", "usuario", "perfil", "deshabilitado", "comentario"],
    "sesiones.csv": ["router", "usuario", "caller_id", "ip"],
    "clientes.csv": [
        "id",
        "nombre",
        "estado",
        "pppoe",
        "serial_onu",
        "acceso",
        "nap",
        "puerto_nap",
        "router",
    ],
}


def write_templates(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, headers in TEMPLATES.items():
        path = directory / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(headers)
        written.append(path)
    return written
