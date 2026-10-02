"""Ejecutor de punta a punta por SSH real (asyncssh) contra el simulador de VSOL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic import SecretStr

from olterra.devtools.vsol_sim import VsolSimulator
from olterra.drivers import get_driver
from olterra.drivers.base import CommandCall
from olterra.drivers.vsol_gpon.parsers import parse_onu_list, parse_version
from olterra.executor.bus import MemoryBus
from olterra.executor.plan import CliCommand, Credential, Plan, Priority, Target
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor
from olterra.orchestrator import build_read_plan, interpret
from olterra.security.sealed import generate_keypair

DRIVER = get_driver("vsol-gpon")
PASSWORD = "olterra-sim"


@pytest.fixture
async def simulator() -> AsyncIterator[tuple[VsolSimulator, int]]:
    sim = VsolSimulator()
    port = await sim.start()
    yield sim, port
    await sim.stop()


def credential(password: str = PASSWORD) -> Credential:
    return Credential(username="admin", password=SecretStr(password))


def plan_for(port: int, calls: list[CommandCall], **kw: object) -> Plan:
    return Plan(
        tenant_id=uuid4(),
        olt_id=uuid4(),
        priority=Priority.USER,
        on_error="continue",
        target=Target(host="127.0.0.1", ssh_port=port, **kw),  # type: ignore[arg-type]
        session=DRIVER.session,
        steps=DRIVER.build_cli_steps(calls),
    )


async def test_read_plan_over_ssh(simulator: tuple[VsolSimulator, int]) -> None:
    sim, port = simulator
    plan = plan_for(port, [CommandCall("system.version"), CommandCall("onu.list", {"pon": 1})])
    result = await PlanRunner("prueba").run(plan, credential())
    assert result.status == "ok", result
    assert result.host_key == sim.public_host_key  # confianza al primer uso
    outputs = {plan.steps[i].command: s.output for i, s in enumerate(result.steps)}  # type: ignore[union-attr]
    assert parse_version(outputs["show version"] or "").model == "V1600G1"
    serials = [r.serial for r in parse_onu_list(outputs["show onu info"] or "", 1)]
    assert serials == ["VSOL0008D09C", "VSOL00A1B2C3", "HWTC1F2E3D4C"]
    assert sim.commands_seen[:3] == ["enable", "terminal length 0", "configure terminal"]


async def test_pagination_without_terminal_length(simulator: tuple[VsolSimulator, int]) -> None:
    sim, port = simulator
    sim.config.terminal_length_supported = False
    sim.config.page_lines = 2
    plan = plan_for(port, [CommandCall("onu.list", {"pon": 1})])
    result = await PlanRunner("prueba").run(plan, credential())
    onu_step = result.steps[2]  # configure terminal, interface gpon 0/1, show onu info
    assert onu_step.ok
    assert len(parse_onu_list(onu_step.output or "", 1)) == 3
    assert "More" not in (onu_step.output or "")


async def test_pinned_host_key_mismatch_is_refused(simulator: tuple[VsolSimulator, int]) -> None:
    _, port = simulator
    impostor = VsolSimulator().public_host_key
    plan = plan_for(port, [CommandCall("system.version")], ssh_host_key=impostor)
    result = await PlanRunner("prueba").run(plan, credential())
    assert result.status == "failed"
    assert "llave SSH" in (result.steps[0].error or "")


async def test_wrong_password_fails_without_leaking_it(
    simulator: tuple[VsolSimulator, int],
) -> None:
    _, port = simulator
    plan = plan_for(port, [CommandCall("system.version")])
    result = await PlanRunner("prueba").run(plan, credential("clave-mala-123"))
    assert result.status == "failed"
    assert "clave-mala-123" not in result.model_dump_json()


async def test_write_plan_stops_on_first_error(simulator: tuple[VsolSimulator, int]) -> None:
    sim, port = simulator
    steps = DRIVER.build_cli_steps(
        [
            CommandCall(
                "onu.authorize", {"pon": 1, "onu": 1, "profile": "HGU", "serial": "VSOL00BEEF01"}
            ),
            CommandCall("onu.reboot", {"pon": 1, "onu": 2}),
        ]
    )
    plan = Plan(
        tenant_id=uuid4(),
        olt_id=uuid4(),
        access="write",
        on_error="stop",
        target=Target(host="127.0.0.1", ssh_port=port),
        session=DRIVER.session,
        steps=steps,
    )
    result = await PlanRunner("prueba").run(plan, credential())
    failed = next(i for i, s in enumerate(result.steps) if not s.ok)
    assert "already exist" in (result.steps[failed].error or "")
    assert all("No se ejecutó" in (s.error or "") for s in result.steps[failed + 1 :])
    assert "onu 2 reboot" not in sim.commands_seen
    assert result.status == "partial"


async def test_full_loop_seal_execute_interpret(simulator: tuple[VsolSimulator, int]) -> None:
    """Nube arma y sella → ejecutor abre y corre → nube interpreta con el parser."""
    _, port = simulator
    private, public = generate_keypair()
    calls = [CommandCall("onu.list", {"pon": 1}), CommandCall("onu.autofind", {"pon": 1})]
    plan = build_read_plan(
        DRIVER,
        tenant_id=uuid4(),
        olt_id=uuid4(),
        target=Target(host="127.0.0.1", ssh_port=port),
        calls=calls,
        credential=credential(),
        executor_public_key=public,
    )
    assert PASSWORD not in plan.model_dump_json()  # la clave viaja sellada
    bus = MemoryBus()
    executor = Executor(bus=bus, runner=PlanRunner("prueba"), group="cloud", private_key=private)
    await executor.handle(await bus.publish_plan(plan))
    [result] = bus.results
    interpreted = interpret(
        [
            {
                "key": c.key,
                "params": dict(c.params),
                "command": DRIVER.command(c.key).render(**c.params),
            }
            for c in calls
        ],
        [s.command for s in plan.steps if isinstance(s, CliCommand)],
        result,
    )
    by_key = {o["key"]: o for o in interpreted["outputs"]}
    assert [row["serial"] for row in by_key["onu.list"]["data"]] == [
        "VSOL0008D09C",
        "VSOL00A1B2C3",
        "HWTC1F2E3D4C",
    ]
    assert by_key["onu.autofind"]["data"][0]["serial"] == "VSOL00BEEF01"
