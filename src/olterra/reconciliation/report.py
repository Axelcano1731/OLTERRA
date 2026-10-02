"""Reportes de la conciliación: HTML para mostrar, CSV para trabajar, JSON para integrar."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from olterra import __version__
from olterra.reconciliation.engine import ReconResult
from olterra.reconciliation.model import KIND_TITLES, SEVERITY_ORDER

_REF_COLUMNS = (
    "customer_id",
    "other_customer_id",
    "pppoe",
    "expected",
    "serial",
    "olt",
    "pon",
    "onu",
    "router",
)


def _env() -> Environment:
    return Environment(
        loader=PackageLoader("olterra.reconciliation", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(
    result: ReconResult, *, title: str = "Conciliación OLT · MikroTik · CRM", isp: str | None = None
) -> str:
    groups = sorted(
        result.by_kind().items(),
        key=lambda item: (SEVERITY_ORDER[item[1][0].severity], -len(item[1]), item[0].value),
    )
    return (
        _env()
        .get_template("report.html.j2")
        .render(
            title=title,
            isp=isp,
            generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            counts=result.counts,
            groups=[(kind, KIND_TITLES[kind], findings) for kind, findings in groups],
            links=len(result.links),
            version=__version__,
        )
    )


def render_csv(result: ReconResult) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["severidad", "tipo", "titulo", "detalle", "accion_sugerida", *_REF_COLUMNS])
    for finding in result.findings:
        writer.writerow(
            [
                finding.severity.value,
                finding.kind.value,
                finding.title,
                finding.detail,
                finding.suggestion,
                *(finding.refs.get(column, "") for column in _REF_COLUMNS),
            ]
        )
    return buffer.getvalue()


def to_dict(result: ReconResult) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": result.counts,
        "findings": [
            {
                "kind": f.kind.value,
                "severity": f.severity.value,
                "title": f.title,
                "detail": f.detail,
                "suggestion": f.suggestion,
                "refs": f.refs,
            }
            for f in result.findings
        ],
    }


def write_reports(
    result: ReconResult, directory: Path, formats: set[str], isp: str | None = None
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    written = []
    if "html" in formats:
        path = directory / f"conciliacion-{stamp}.html"
        path.write_text(render_html(result, isp=isp), encoding="utf-8")
        written.append(path)
    if "csv" in formats:
        path = directory / f"conciliacion-{stamp}.csv"
        # utf-8-sig para que Excel en Windows muestre bien las tildes.
        path.write_text(render_csv(result), encoding="utf-8-sig", newline="")
        written.append(path)
    if "json" in formats:
        path = directory / f"conciliacion-{stamp}.json"
        path.write_text(json.dumps(to_dict(result), ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written
