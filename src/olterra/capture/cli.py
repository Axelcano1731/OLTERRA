"""``olterra-capture``: corre el catálogo de comandos de SOLO LECTURA contra una OLT y
guarda cada salida, con un manifiesto, para volverla prueba del parser.

    olterra-capture --host 192.168.8.200 --usuario admin --pon 1 --pon 2 --onu 1:1 \
        --snmp-comunidad olterra-lab --anonimizar --salida lab-captures

La clave se toma de ``OLTERRA_CAPTURE_PASSWORD`` o se pide por teclado; nunca va
en la línea de comandos (quedaría en el historial).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from olterra import __version__
from olterra.capture.anonymize import Anonymizer
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall, CommandTemplate, Driver, UnrecognizedOutput
from olterra.drivers.vsol_gpon import snmp as vsol_snmp
from olterra.drivers.vsol_gpon.parsers import parse_version
from olterra.executor.plan import Credential, Plan, PlanResult, Priority, SnmpWalk, Target
from olterra.executor.runner import PlanRunner
from olterra.orchestrator import pair_outputs
from olterra.security.masking import redact

PROFILE_KINDS = ("onu", "dba", "traffic", "line", "srv")
PON_KEYS = (
    "mac.by_pon",
    "pon.info",
    "pon.optical",
    "pon.statistics",
    "onu.autolearn",
    "onu.autofind",
    "onu.autofind_detail",
    "onu.list",
    "onu.rx_power_all",
    "onu.state",
)
ONU_KEYS = (
    "onu.detail",
    "onu.optical",
    "onu.capability",
    "onu.service_config",
    "onu.description",
    "onu.distance",
)
SNMP_WALKS = {
    "sysDescr": vsol_snmp.SYS_DESCR,
    "ifDescr": "1.3.6.1.2.1.2.2.1.2",
    "ifAlias": vsol_snmp.IF_ALIAS,
    "gOnuStaInfoTable": vsol_snmp.STATUS_TABLE,
    "gOnuAuthInfoTable": vsol_snmp.AUTH_TABLE,
    "gOnuOpticalInfoTable": vsol_snmp.OPTICAL_TABLE,
    "gOnuDetailInfoTable": vsol_snmp.DETAIL_TABLE,
    "gOnuRttTable": vsol_snmp.RTT_TABLE,
    "ponTransceiverTable": vsol_snmp.PON_TRANSCEIVER_TABLE,
}


@dataclass(frozen=True)
class CaptureCall:
    template: CommandTemplate
    params: dict[str, Any]

    @property
    def command(self) -> str:
        return self.template.render(**self.params)

    def filename(self) -> str:
        suffix = "".join(
            f"_{name}{self.params[name]}" for name in ("kind", "pon", "onu") if name in self.params
        )
        return f"cli/{self.template.key}{suffix}.txt"


def plan_calls(
    driver: Driver,
    pons: list[int],
    onus: list[tuple[int, int]],
    model: str | None = None,
    firmware: str | None = None,
) -> list[CaptureCall]:
    """Llamadas de solo lectura, agrupadas por modo para no saltar de un PON a otro.

    Con ``model`` y ``firmware`` se usa la sintaxis propia de ese modelo (overrides del driver);
    sin ellos, la del manual.
    """

    def template(key: str) -> CommandTemplate:
        return driver.command(key, model, firmware)

    calls: list[CaptureCall] = []
    for base in driver.read_only_catalog():
        placeholders = set(base.placeholders())
        if base.mode.value != "pon" and not placeholders:
            calls.append(CaptureCall(template(base.key), {}))
    for kind in PROFILE_KINDS:
        calls.append(CaptureCall(template("profile.list"), {"kind": kind}))
    for pon in pons:
        for key in PON_KEYS:
            calls.append(CaptureCall(template(key), {"pon": pon}))
        for onu_pon, onu in onus:
            if onu_pon == pon:
                for key in ONU_KEYS:
                    calls.append(CaptureCall(template(key), {"pon": pon, "onu": onu}))
    return calls


def _safe_path_part(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", value or "desconocido")[:40] or "desconocido"


def _map_outputs(
    calls: list[CaptureCall], plan: Plan, result: PlanResult
) -> list[tuple[CaptureCall, Any]]:
    """Empareja cada llamada con el resultado de su paso (los cambios de modo se saltan)."""
    step_commands = [getattr(step, "command", "") for step in plan.steps]
    pairs = pair_outputs([c.command for c in calls], step_commands, result.steps)
    return [(calls[index], step_result) for index, step_result in pairs]


async def run_capture(args: argparse.Namespace, password: str, enable_password: str | None) -> Path:
    driver = get_driver(args.driver)
    onus = [tuple(int(x) for x in item.split(":")) for item in args.onu]
    calls = plan_calls(
        driver, sorted(set(args.pon)), [(p, o) for p, o in onus], args.modelo, args.firmware_olt
    )
    credential = Credential(
        username=args.usuario,
        password=SecretStr(password),
        enable_password=SecretStr(enable_password) if enable_password else None,
        snmp_community=SecretStr(args.snmp_comunidad) if args.snmp_comunidad else None,
    )
    target = Target(host=args.host, ssh_port=args.puerto, snmp_port=args.puerto_snmp)
    session = driver.session.model_copy(update={"legacy_ssh_algorithms": args.ssh_legacy})
    tenant_id, olt_id = uuid4(), uuid4()
    cli_plan = Plan(
        tenant_id=tenant_id,
        olt_id=olt_id,
        priority=Priority.USER,
        access="read",
        on_error="continue",
        target=target,
        session=session,
        steps=driver.build_cli_steps(
            [CommandCall(c.template.key, c.params) for c in calls],
            model=args.modelo,
            firmware=args.firmware_olt,
            timeout_s=args.timeout,
        ),
    )
    runner = PlanRunner("olterra-capture")
    print(f"Capturando {len(calls)} comandos de solo lectura en {args.host}...", file=sys.stderr)
    cli_result = await runner.run(cli_plan, credential)
    if (
        cli_result.status == "failed"
        and cli_result.steps
        and not any(s.ok for s in cli_result.steps)
    ):
        first_error = next((s.error for s in cli_result.steps if s.error), cli_result.error)
        raise SystemExit(f"No se pudo capturar: {first_error}")

    snmp_result: PlanResult | None = None
    if args.snmp_comunidad:
        snmp_plan = Plan(
            tenant_id=tenant_id,
            olt_id=olt_id,
            priority=Priority.USER,
            on_error="continue",
            target=target,
            steps=[SnmpWalk(oid=oid, timeout_s=10) for oid in SNMP_WALKS.values()],
        )
        snmp_result = await runner.run(snmp_plan, credential)

    pairs = _map_outputs(calls, cli_plan, cli_result)
    secrets = credential.secret_values()
    anonymizer = Anonymizer() if args.anonimizar else None

    def clean(text: str) -> str:
        text = redact(text, secrets)
        return anonymizer.text(text) if anonymizer else text

    version_text = next(
        (r.output for c, r in pairs if c.template.key == "system.version" and r.output), ""
    )
    try:
        version = parse_version(version_text)
        model, firmware = version.model, version.firmware
    except UnrecognizedOutput:
        model = firmware = None
    if snmp_result and not model:
        sys_descr = next((v.value for s in snmp_result.steps for v in (s.varbinds or [])), "")
        model = vsol_snmp.detect_model(sys_descr)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = (
        Path(args.salida) / args.driver / _safe_path_part(model) / _safe_path_part(firmware) / stamp
    )
    (out_dir / "cli").mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "olterra_version": __version__,
        "driver": args.driver,
        "captured_at": datetime.now(UTC).isoformat(),
        "model": model,
        "firmware": firmware,
        "anonymized": bool(anonymizer),
        "host_key": cli_result.host_key,
        "commands": [],
        "snmp": [],
    }
    for call, step_result in pairs:
        entry: dict[str, Any] = {
            "key": call.template.key,
            "command": call.command,
            "pon": call.params.get("pon"),
            "onu": call.params.get("onu"),
            "ok": step_result.ok,
            "error": clean(step_result.error) if step_result.error else None,
            "elapsed_ms": step_result.elapsed_ms,
            "source": call.template.source,
            "file": None,
        }
        if step_result.output is not None:
            path = out_dir / call.filename()
            path.write_text(clean(step_result.output) + "\n", encoding="utf-8", newline="\n")
            entry["file"] = call.filename()
        manifest["commands"].append(entry)

    if snmp_result is not None:
        (out_dir / "snmp").mkdir(exist_ok=True)
        for (name, oid), step_result in zip(SNMP_WALKS.items(), snmp_result.steps, strict=False):
            entry = {
                "name": name,
                "oid": oid,
                "ok": step_result.ok,
                "error": step_result.error,
                "rows": 0,
                "file": None,
            }
            if step_result.varbinds:
                lines = [f"{v.oid}|{v.type}|{clean(v.value)}" for v in step_result.varbinds]
                (out_dir / "snmp" / f"{name}.snmprec").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
                )
                entry.update(rows=len(lines), file=f"snmp/{name}.snmprec")
            manifest["snmp"].append(entry)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    _print_summary(manifest, out_dir)
    return out_dir


def _print_summary(manifest: dict[str, Any], out_dir: Path) -> None:
    commands = manifest["commands"]
    ok = [c for c in commands if c["ok"]]
    failed = [c for c in commands if not c["ok"]]
    print(f"\nModelo: {manifest['model'] or '?'}   Firmware: {manifest['firmware'] or '?'}")
    print(f"Comandos: {len(ok)} respondieron, {len(failed)} con error")
    for entry in failed:
        print(f"  [x] {entry['command']:<45} {entry['error'] or ''}")
    for entry in manifest["snmp"]:
        mark = "[ok]" if entry["rows"] else "[x] "
        print(f"  {mark} SNMP {entry['name']:<22} {entry['rows']} filas {entry['error'] or ''}")
    print(f"\nCaptura en {out_dir}")
    print("Antes de copiarla a tests/fixtures, revise a mano descripciones y nombres de clientes.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="olterra-capture", description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--usuario", required=True)
    parser.add_argument("--puerto", type=int, default=22)
    parser.add_argument("--puerto-snmp", type=int, default=161)
    parser.add_argument("--driver", default="vsol-gpon")
    parser.add_argument(
        "--pon", type=int, action="append", default=[], help="PON a capturar (se repite)"
    )
    parser.add_argument(
        "--onu",
        action="append",
        default=[],
        help="Muestra PON:ONU para comandos por ONU (se repite)",
    )
    parser.add_argument(
        "--modelo",
        help="Modelo de la OLT (V1600G0-B): usa su sintaxis propia en vez de la del manual",
    )
    parser.add_argument("--firmware-olt", help="Firmware de la OLT (V1.4.8R), junto con --modelo")
    parser.add_argument("--snmp-comunidad", help="Si se da, también se recorren las tablas SNMP")
    parser.add_argument(
        "--anonimizar", action="store_true", help="Reemplaza seriales y MAC por valores falsos"
    )
    parser.add_argument("--ssh-legacy", action="store_true", help="Permite algoritmos SSH viejos")
    parser.add_argument("--timeout", type=float, default=60.0, help="Segundos por comando")
    parser.add_argument("--salida", default="lab-captures")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for item in args.onu:
        if not re.fullmatch(r"\d+:\d+", item):
            raise SystemExit(f"--onu espera PON:ONU (p. ej. 1:5), no '{item}'")
    password = os.environ.get("OLTERRA_CAPTURE_PASSWORD") or getpass.getpass(
        "Clave SSH de la OLT: "
    )
    enable = os.environ.get("OLTERRA_CAPTURE_ENABLE_PASSWORD") or None
    asyncio.run(run_capture(args, password, enable))


if __name__ == "__main__":
    main()
