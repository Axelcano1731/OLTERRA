"""Alta de punta a punta contra el simulador por SSH: el operador solo da cliente, plan y PPPoE.

Olterra busca la posición libre, autoriza y guarda, espera a que la ONU se conecte, pone la WAN y
el WiFi con los puertos que tiene ese modelo y comprueba la señal. Las claves del cliente no
quedan en ninguna parte.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from olterra.api import jobs
from olterra.db.models import AuditLog, PlanRun, ProvisionJob, ProvisionTemplate
from olterra.db.session import create_engine, session_factory
from olterra.executor.bus import MemoryBus
from olterra.executor.plan import CliCommand, Plan
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor
from tests.integration.test_api import (  # noqa: F401 - fixtures y ayudas compartidas
    Env,
    SimulatorThread,
    create_olt,
    env,
)

pytestmark = pytest.mark.postgres

PPPOE_KEY, WIFI_KEY = "Ppp#Sim-2026", "Wifi#Sim-2026"
ONU_ADMIN_KEY = "Admin#Onu-2026"
TEMPLATE = {
    "auth_profile": "default",
    "tconts": [{"id": 1, "name": "INTERNET", "dba": "default1"}],
    "gemports": [{"id": 1, "tcont": 1, "name": "INTERNET", "limit_down": None}],
    "services": [{"name": "ser_1", "gemport": 1, "vlan": 111}],
    "service_ports": [{"id": 1, "gemport": 1, "user_vlan": 111, "vlan": 111, "cos": 0}],
    "wan": {"vlan": 111, "binds": ["lan1", "lan2", "lan3", "lan4", "ssid1"]},
    "wifi": {"ssid_index": 1},
    "management": {
        "firewall": "low",
        "ping_wan": True,
        "wan_access": ["http", "https"],
        "admin_user": "soporte",
    },
}


def run_plans(env: Env, done: set[UUID]) -> int:  # noqa: F811
    """Ejecuta contra el simulador los planes publicados que falten y entrega sus resultados."""
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    ran = 0
    for plan in list(env.publisher.plans):
        if plan.plan_id in done:
            continue
        done.add(plan.plan_id)

        async def execute(plan: Plan = plan) -> Any:
            bus = MemoryBus()
            executor = Executor(
                bus=bus,
                runner=PlanRunner("prueba"),
                group="cloud",
                private_key=env.executor_private_key,
            )
            await executor.handle(await bus.publish_plan(plan))
            return bus.results[0]

        result = asyncio.run(execute())
        env.client.portal.call(state.store_result, result)  # type: ignore[union-attr]
        ran += 1
    return ran


def drive(env: Env, job_id: str) -> dict[str, Any]:  # noqa: F811
    """Corre el trabajo hasta que termine (sin esperar los 15 s entre consultas)."""
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    done: set[UUID] = set()
    for _ in range(30):
        run_plans(env, done)
        job = env.client.get(f"/v1/provision-jobs/{job_id}", headers=env.headers()).json()
        if job["status"] != "running":
            return job
        env.client.portal.call(jobs.tick, state)  # type: ignore[union-attr]
    raise AssertionError(f"El trabajo no terminó: {job}")


@pytest.fixture
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "POLL_EVERY", timedelta(0))


def test_easy_authorize_end_to_end(env: Env, no_wait: None) -> None:  # noqa: F811
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    state.settings.allow_unverified_writes = True
    try:
        with SimulatorThread() as simulator:
            olt = create_olt(
                env,
                real_ip="127.0.0.1",
                ssh_port=simulator.port,
                model="V1600G0-B",
                firmware="V1.4.8R",
                enable_password="olterra-sim",
            )
            # Sin la contraseña de administración de la ONU el plan no se guarda.
            missing = env.client.post(
                "/v1/provision-templates",
                json={"name": "Hogar VLAN 111", "body": TEMPLATE},
                headers=env.headers(),
            )
            assert missing.status_code == 400
            assert "contraseña de administración" in missing.json()["detail"]
            template = env.client.post(
                "/v1/provision-templates",
                json={
                    "name": "Hogar VLAN 111",
                    "body": TEMPLATE,
                    "onu_admin_password": ONU_ADMIN_KEY,
                },
                headers=env.headers(),
            ).json()
            assert template["onu_admin_password_set"] is True
            assert not template["onu_user_password_set"]
            # Editar el plan sin escribirla otra vez la conserva.
            renamed = env.client.put(
                f"/v1/provision-templates/{template['id']}",
                json={"name": "Hogar VLAN 111", "body": TEMPLATE},
                headers=env.headers(),
            )
            assert renamed.status_code == 200 and renamed.json()["onu_admin_password_set"]
            assert ONU_ADMIN_KEY not in renamed.text

            # 1) Buscar: todos los PON y la lista de clientes, en una sola lectura.
            scan = env.client.post(f"/v1/olts/{olt['id']}/onus/scan", headers=env.headers())
            assert scan.status_code == 202, scan.text
            run_plans(env, set())
            outputs = env.client.get(
                f"/v1/plans/{scan.json()['plan_id']}", headers=env.headers()
            ).json()["result"]["outputs"]
            brief = next(o for o in outputs if o["key"] == "interfaces.brief")["data"]
            assert brief["pons"] == list(range(1, 9))
            new = [r["serial"] for o in outputs if o["key"] == "onu.autofind" for r in o["data"]]
            assert new == ["VSOL00BEEF01", "ZTEGC0A1B2C3"]
            assert env.client.get(f"/v1/olts/{olt['id']}", headers=env.headers()).status_code == 200

            # 2) Autorizar: nombre como lo escribe el operador, nada técnico.
            response = env.client.post(
                f"/v1/olts/{olt['id']}/onus/authorize",
                json={
                    "template_id": template["id"],
                    "pon": 1,
                    "serial": "VSOL00BEEF01",
                    # Así llega del autofind de la V1600G0-B cuando la OLT no sabe el modelo.
                    "equipment_id": "NULL",
                    "customer": "José Pérez Núñez",
                    "pppoe_user": "jose.perez",
                    "pppoe_password": PPPOE_KEY,
                    "wifi_name": "Casa Pérez",
                    "wifi_key": WIFI_KEY,
                },
                headers=env.headers(),
            )
            assert response.status_code == 202, response.text
            job = drive(env, response.json()["id"])

            assert job["status"] == "done", job
            assert (job["pon"], job["onu"]) == (1, 4)  # el sim tiene 1, 2 y 3 ocupadas
            assert job["description"] == "Jose_Perez_Nunez"
            assert job["wifi_ssid"] == "Casa_Perez"
            assert (
                job["equipment_id"] == "VSOLV422"
            )  # del detalle de la ONU (no venía del autofind)
            assert job["rx_dbm"] == -19.5
            assert [s["status"] for s in job["steps"]] == ["done"] * 5

            new_onu = next(o for o in simulator.sim.config.onus if o.onu == 4)
            assert new_onu.serial == "VSOL00BEEF01" and new_onu.description == "Jose_Perez_Nunez"
            # La V422 del simulador tiene 2 LAN: la WAN no se amarra a lan3 ni lan4.
            assert "onu 4 pri wan_adv index 1 bind lan1 lan2 ssid1" in new_onu.config
            assert "onu 4 pri equid VSOLV422" in new_onu.config
            # Gestión remota: firewall bajo, ping y web abiertos desde internet; telnet cerrado.
            acl = "control enable lan enable wan {} ipv4_control disable ipv6_control disable"
            assert "onu 4 pri firewall level low" in new_onu.config
            assert f"onu 4 pri acl https {acl.format('enable')}" in new_onu.config
            assert f"onu 4 pri acl telnet {acl.format('disable')}" in new_onu.config
            # La cuenta de administración cambia antes de abrir la web.
            account = (
                f"onu 4 pri username admin_control enable soporte {ONU_ADMIN_KEY} "
                "user_control disable"
            )
            assert account in simulator.sim.commands_seen
            assert simulator.sim.commands_seen.index(account) < simulator.sim.commands_seen.index(
                f"onu 4 pri acl https {acl.format('enable')}"
            )
            assert not any("equid NULL" in c for c in simulator.sim.commands_seen)

            # "Internet y WiFi" otra vez sobre la misma ONU: reescribe la WAN, no crea otra.
            before = len(env.publisher.plans)
            again = env.client.post(
                f"/v1/olts/{olt['id']}/onus/configure",
                json={
                    "template_id": template["id"],
                    "pon": 1,
                    "onu": 4,
                    "pppoe_user": "jose.perez2",
                    "pppoe_password": PPPOE_KEY,
                },
                headers=env.headers(),
            )
            assert again.status_code == 202, again.text
            redo = drive(env, again.json()["id"])
            assert redo["status"] == "done", redo
            redone = [
                step.command
                for plan in env.publisher.plans[before:]
                for step in plan.steps
                if isinstance(step, CliCommand)
            ]
            assert "onu 4 pri wan_adv add route" not in redone
            assert any("user jose.perez2 pwd" in c for c in redone)
            assert any(f"pwd {PPPOE_KEY}" in c for c in simulator.sim.commands_seen)
            # La WAN entra después de guardar el servicio, no en el mismo plan que "onu add".
            seen = simulator.sim.commands_seen
            assert (
                seen.index("onu 4 service-port 1 gemport 1 uservlan 111 vlan 111 new_cos 0")
                < (seen.index("write"))
                < seen.index("onu 4 pri wan_adv add route")
            )
    finally:
        state.settings.allow_unverified_writes = False

    # Ni en los planes publicados, ni en la base, ni en la bitácora, ni en el trabajo.
    published = json.dumps([p.model_dump_json() for p in env.publisher.plans])
    assert PPPOE_KEY not in published and WIFI_KEY not in published
    assert ONU_ADMIN_KEY not in published

    async def stored() -> tuple[str, Any]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                runs = (await session.execute(select(PlanRun.calls, PlanRun.result))).all()
                audits = (await session.execute(select(AuditLog.after))).scalars().all()
                row = await session.get(ProvisionJob, UUID(job["id"]))
                saved = await session.get(ProvisionTemplate, UUID(template["id"]))
                assert saved is not None and saved.secrets  # cifrada, no vacía
                plain = [str(saved.body) + str(saved.secrets)]
                return (
                    json.dumps([list(map(str, r)) for r in runs])
                    + json.dumps(list(audits))
                    + json.dumps(plain),
                    row,
                )
        finally:
            await engine.dispose()

    dumped, row = asyncio.run(stored())
    assert PPPOE_KEY not in dumped and WIFI_KEY not in dumped
    assert ONU_ADMIN_KEY not in dumped
    assert row is not None and row.secrets is None  # las claves cifradas se borran al terminar


def test_authorize_explains_problems_in_plain_words(env: Env, no_wait: None) -> None:  # noqa: F811
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    state.settings.allow_unverified_writes = True
    try:
        with SimulatorThread() as simulator:
            olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port, model="V1600G0-B")
            template = env.client.post(
                "/v1/provision-templates",
                json={"name": "Plan B", "body": TEMPLATE, "onu_admin_password": ONU_ADMIN_KEY},
                headers=env.headers(),
            ).json()
            url = f"/v1/olts/{olt['id']}/onus/authorize"
            base = {"template_id": template["id"], "pon": 1, "customer": "Ana"}

            # Falta el PPPoE: se dice antes de tocar la OLT.
            missing = env.client.post(
                url, json=base | {"serial": "VSOL00BEEF01"}, headers=env.headers()
            )
            assert missing.status_code == 400 and "PPPoE" in missing.text

            # Una ONU que ya está autorizada (la 1/1 del simulador).
            again = env.client.post(
                url,
                json=base | {"serial": "VSOL0008D09C", "pppoe_user": "ana", "pppoe_password": "x1"},
                headers=env.headers(),
            )
            job = drive(env, again.json()["id"])
            assert job["status"] == "failed"
            assert "ya está autorizada en el PON 1, posición 1" in job["error"]
            assert [s["status"] for s in job["steps"]][:2] == ["failed", "pending"]
    finally:
        state.settings.allow_unverified_writes = False
