"""Cambiar la clave de la OLT desde Olterra, de punta a punta contra el simulador por SSH.

La OLT pregunta la clave nueva después del Enter; el ejecutor la contesta desde el sello. Olterra
no guarda nada (ni en la OLT ni en su bóveda) hasta entrar con la clave nueva en otra sesión.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from olterra.db.models import AuditLog, PlanRun, ProvisionJob
from olterra.db.session import create_engine, session_factory
from tests.integration.test_api import (  # noqa: F401 - fixtures y ayudas compartidas
    Env,
    SimulatorThread,
    create_olt,
    env,
)
from tests.integration.test_provision_jobs import drive, no_wait, run_plans  # noqa: F401

pytestmark = pytest.mark.postgres

NEW_LOGIN, NEW_ENABLE = "Nueva#Sim-2026", "Enable#Sim-2026"


def lab_mode(env: Env) -> Any:  # noqa: F811
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    state.settings.allow_unverified_writes = True
    return state


def olt_on(env: Env, simulator: SimulatorThread) -> dict[str, Any]:  # noqa: F811
    return create_olt(
        env,
        real_ip="127.0.0.1",
        ssh_port=simulator.port,
        model="V1600G0-B",
        firmware="V1.4.8R",
        enable_password="olterra-sim",
    )


def version_query(env: Env, olt_id: str) -> dict[str, Any]:  # noqa: F811
    """Una consulta con la credencial que Olterra tenga guardada en ese momento."""
    plan = env.client.post(
        f"/v1/olts/{olt_id}/queries",
        json={"commands": ["system.version"]},
        headers=env.headers(),
    )
    assert plan.status_code == 202, plan.text
    run_plans(env, set())
    return dict(env.client.get(f"/v1/plans/{plan.json()['plan_id']}", headers=env.headers()).json())


async def _rows(env: Env, job_id: str) -> tuple[Any, list[Any], list[Any]]:  # noqa: F811
    engine = create_engine(env.pg.owner_url)
    try:
        async with session_factory(engine)() as session:
            job = await session.get(ProvisionJob, UUID(job_id))
            runs = (await session.execute(select(PlanRun))).scalars().all()
            audits = (await session.execute(select(AuditLog))).scalars().all()
            return job, list(runs), list(audits)
    finally:
        await engine.dispose()


def test_change_password_end_to_end(env: Env, no_wait: None) -> None:  # noqa: F811
    state = lab_mode(env)
    try:
        with SimulatorThread() as simulator:
            olt = olt_on(env, simulator)
            started = env.client.post(
                f"/v1/olts/{olt['id']}/password",
                json={"new_password": NEW_LOGIN, "new_enable_password": NEW_ENABLE},
                headers=env.headers(),
            )
            assert started.status_code == 202, started.text
            assert started.json()["kind"] == "olt_password"
            assert started.json()["username"] == "admin"
            assert started.json()["unverified"]  # modo laboratorio: lo dice
            assert NEW_LOGIN not in started.text

            # Mientras corre, otro cambio de clave en la misma OLT se rechaza.
            again = env.client.post(
                f"/v1/olts/{olt['id']}/password",
                json={"new_password": NEW_LOGIN},
                headers=env.headers(),
            )
            assert again.status_code == 409

            job = drive(env, started.json()["id"])
            assert job["status"] == "done", job
            assert [s["status"] for s in job["steps"]] == ["done", "done", "done"]

            # La OLT tiene las claves nuevas, y lo que se escribió fue la pregunta, no la línea.
            assert simulator.sim.config.password == NEW_LOGIN
            assert simulator.sim.config.enable_password == NEW_ENABLE
            seen = simulator.sim.commands_seen
            assert "user login-password admin" in seen
            assert "user enable-password admin" in seen
            assert "write" in seen

            # Olterra ya entra con las nuevas (la anterior la OLT la rechaza).
            result = version_query(env, olt["id"])
            assert result["status"] == "ok", result

            row, runs, audits = asyncio.run(_rows(env, job["id"]))
            assert row.secrets is None  # las claves nuevas no se quedan en el trabajo
            dumped = (
                json.dumps([[r.calls, r.commands, r.result] for r in runs])
                + json.dumps([[a.before, a.after] for a in audits])
                + json.dumps([p.model_dump_json() for p in env.publisher.plans])
            )
            for secret in (NEW_LOGIN, NEW_ENABLE, "olterra-sim"):
                assert secret not in dumped
            change = next(a for a in audits if a.action == "olt.password_change")
            assert change.after["changed"] == ["login", "enable"]
            assert change.after["saved"] is True
    finally:
        state.settings.allow_unverified_writes = False


def test_password_is_not_stored_if_the_new_one_does_not_log_in(
    env: Env,  # noqa: F811
    no_wait: None,  # noqa: F811
) -> None:
    state = lab_mode(env)
    try:
        with SimulatorThread() as simulator:
            simulator.sim.config.password_change_applies = False  # dice que sí y no la cambia
            olt = olt_on(env, simulator)
            started = env.client.post(
                f"/v1/olts/{olt['id']}/password",
                json={"new_password": NEW_LOGIN},
                headers=env.headers(),
            )
            job = drive(env, started.json()["id"])
            assert job["status"] == "failed"
            assert "No se pudo entrar con la clave nueva" in job["error"]
            assert "sigue aceptando la clave anterior" in job["error"]
            assert [s["status"] for s in job["steps"]] == ["done", "failed", "pending"]
            # No se guardó nada en la OLT y Olterra sigue entrando con la anterior.
            assert "write" not in simulator.sim.commands_seen
            assert version_query(env, olt["id"])["status"] == "ok"
    finally:
        state.settings.allow_unverified_writes = False


def test_password_change_needs_lab_mode_and_a_valid_password(env: Env) -> None:  # noqa: F811
    olt = create_olt(env, real_ip="127.0.0.1", model="V1600G0-B", firmware="V1.4.8R")
    url = f"/v1/olts/{olt['id']}/password"
    refused = env.client.post(url, json={"new_password": NEW_LOGIN}, headers=env.headers())
    assert refused.status_code == 400
    assert "no está validado en laboratorio" in refused.json()["detail"]
    for body in ({}, {"new_password": "corta"}, {"new_password": "con espacio 2026"}):
        assert env.client.post(url, json=body, headers=env.headers()).status_code == 422
