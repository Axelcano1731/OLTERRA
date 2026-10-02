"""``olterra-conciliar``: descuadres OLT ↔ MikroTik ↔ CRM con acción sugerida.

    olterra-conciliar demo
    olterra-conciliar plantillas --salida plantillas/
    olterra-conciliar correr --onus onus.csv --secretos secret-export.rsc \
        --sesiones active-terse.txt --clientes clientes.csv --isp "ISP Piloto"

Las claves (MikroTik, ISPWatch) se leen de variables de entorno, nunca de la
línea de comandos: ``OLTERRA_ROUTEROS_PASSWORD`` y ``OLTERRA_ISPWATCH_TOKEN``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from olterra.devtools.demo_recon import demo_input
from olterra.reconciliation.engine import ReconResult, reconcile
from olterra.reconciliation.model import (
    CrmCustomer,
    OnuRecord,
    PppoeSecret,
    PppoeSession,
    ReconInput,
)
from olterra.reconciliation.report import write_reports
from olterra.reconciliation.sources import SourceError
from olterra.reconciliation.sources.tabular import (
    customers_from_rows,
    onus_from_rows,
    read_rows,
    secrets_from_rows,
    sessions_from_rows,
    write_templates,
)


def _is_csv(path: Path) -> bool:
    return path.suffix.lower() in (".csv", ".tsv")


def _load_secrets(paths: list[Path], router: str) -> list[PppoeSecret]:
    from olterra.reconciliation.sources.routeros_text import secrets_from_text

    result: list[PppoeSecret] = []
    for path in paths:
        if _is_csv(path):
            result += secrets_from_rows(read_rows(path), path.name)
        else:
            result += secrets_from_text(
                path.read_text(encoding="utf-8", errors="replace"), router or path.stem
            )
    return result


def _load_sessions(paths: list[Path], router: str) -> list[PppoeSession]:
    from olterra.reconciliation.sources.routeros_text import sessions_from_text

    result: list[PppoeSession] = []
    for path in paths:
        if _is_csv(path):
            result += sessions_from_rows(read_rows(path), path.name)
        else:
            result += sessions_from_text(
                path.read_text(encoding="utf-8", errors="replace"), router or path.stem
            )
    return result


def _load_input(args: argparse.Namespace) -> ReconInput:
    onus: list[OnuRecord] = []
    for path in args.onus:
        onus += onus_from_rows(read_rows(path), path.name)
    for directory in args.onus_captura:
        from olterra.reconciliation.sources.olt_capture import onus_from_capture

        onus += onus_from_capture(directory, args.olt_nombre or directory.name)

    secrets = _load_secrets(args.secretos, args.router_nombre)
    sessions = _load_sessions(args.sesiones, args.router_nombre)
    if args.routeros:
        from olterra.reconciliation.sources.routeros_api import RouterOsTarget, read_pppoe

        password = os.environ.get("OLTERRA_ROUTEROS_PASSWORD")
        if not password:
            raise SourceError("Defina OLTERRA_ROUTEROS_PASSWORD para leer el MikroTik")
        host, _, port = args.routeros.partition(":")
        live_secrets, live_sessions = read_pppoe(
            RouterOsTarget(
                name=args.router_nombre or host,
                host=host,
                username=args.routeros_usuario,
                password=password,
                port=int(port) if port else (8729 if args.routeros_tls else 8728),
                use_tls=args.routeros_tls,
            )
        )
        secrets += live_secrets
        sessions += live_sessions

    customers: list[CrmCustomer] = []
    for path in args.clientes:
        customers += customers_from_rows(read_rows(path), path.name)
    if args.ispwatch_url:
        from olterra.reconciliation.sources.ispwatch import fetch_customers

        token = os.environ.get("OLTERRA_ISPWATCH_TOKEN")
        if not token:
            raise SourceError(
                "Defina OLTERRA_ISPWATCH_TOKEN con una llave read:customers de ISPWatch"
            )
        customers += fetch_customers(args.ispwatch_url, token)
    return ReconInput(onus=onus, secrets=secrets, sessions=sessions, customers=customers)


def _report(result: ReconResult, args: argparse.Namespace) -> None:
    formats = {f.strip().lower() for f in args.formato.split(",") if f.strip()}
    counts = result.counts
    print(
        f"ONUs {counts['onus']} · secretos {counts['secretos']} · sesiones {counts['sesiones']}"
        f" · clientes {counts['clientes']}"
    )
    print(
        f"Hallazgos: {counts['error']} errores, {counts['advertencia']} advertencias,"
        f" {counts['info']} por documentar"
    )
    for path in write_reports(result, Path(args.salida), formats, isp=args.isp):
        print(f"  -> {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="olterra-conciliar", description="Conciliación OLT ↔ MikroTik ↔ CRM"
    )
    sub = parser.add_subparsers(dest="accion", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--salida", default="reportes", help="Directorio de los reportes")
        p.add_argument(
            "--formato", default="html,csv", help="html, csv y/o json, separados por coma"
        )
        p.add_argument("--isp", help="Nombre del ISP para el encabezado del reporte")

    demo = sub.add_parser("demo", help="Reporte con datos sintéticos (para mostrar a pilotos)")
    common(demo)

    templates = sub.add_parser("plantillas", help="Escribe CSV vacíos con las columnas esperadas")
    templates.add_argument("--salida", default="plantillas")

    run = sub.add_parser("correr", help="Concilia con datos reales")
    common(run)
    run.add_argument(
        "--onus", type=Path, action="append", default=[], help="CSV de ONUs (se repite)"
    )
    run.add_argument(
        "--onus-captura",
        type=Path,
        action="append",
        default=[],
        help="Directorio de olterra-capture",
    )
    run.add_argument("--olt-nombre", help="Nombre de la OLT de la captura")
    run.add_argument(
        "--secretos", type=Path, action="append", default=[], help="CSV o '/ppp secret export'"
    )
    run.add_argument(
        "--sesiones", type=Path, action="append", default=[], help="CSV o '/ppp active print terse'"
    )
    run.add_argument(
        "--router-nombre", default="", help="Nombre del MikroTik para los textos exportados"
    )
    run.add_argument("--routeros", help="HOST[:PUERTO] del MikroTik para leer en vivo por la API")
    run.add_argument("--routeros-usuario", default="olterra")
    run.add_argument("--routeros-tls", action="store_true", help="API-SSL (8729)")
    run.add_argument(
        "--clientes", type=Path, action="append", default=[], help="CSV de clientes del CRM"
    )
    run.add_argument(
        "--ispwatch-url", help="URL de ISPWatch para leer clientes por la API de socios"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        if args.accion == "plantillas":
            for path in write_templates(Path(args.salida)):
                print(f"  -> {path}")
            return
        data = demo_input() if args.accion == "demo" else _load_input(args)
        if args.accion == "demo":
            args.isp = args.isp or "ISP de demostración (datos sintéticos)"
        _report(reconcile(data), args)
    except SourceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
