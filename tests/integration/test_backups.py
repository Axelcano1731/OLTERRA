"""Respaldos de la configuración y bitácora, de punta a punta contra el simulador por SSH."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, update

from olterra.admin import create_api_key
from olterra.api import backups
from olterra.db.models import OltBackup, PlanRun
from olterra.db.session import create_engine, session_factory
from tests.integration.test_api import (  # noqa: F401 - fixtures y ayudas compartidas
    Env,
    SimulatorThread,
    create_olt,
    env,
)
from tests.integration.test_provision_jobs import run_plans

pytestmark = pytest.mark.postgres


def backup_now(env: Env, olt_id: str, done: set[UUID]) -> dict[str, Any]:  # noqa: F811
    started = env.client.post(f"/v1/olts/{olt_id}/backups", headers=env.headers())
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "pending"
    run_plans(env, done)
    return dict(env.client.get(f"/v1/backups/{started.json()['id']}", headers=env.headers()).json())


async def _owner(env: Env, fn: Any) -> Any:  # noqa: F811
    engine = create_engine(env.pg.owner_url)
    try:
        async with session_factory(engine)() as session, session.begin():
            return await fn(session)
    finally:
        await engine.dispose()


def test_backups_end_to_end(env: Env) -> None:  # noqa: F811
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    done: set[UUID] = set()
    with SimulatorThread() as simulator:
        olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port)

        # 1) El primero: toda la configuración, con las claves tapadas.
        first = backup_now(env, olt["id"], done)
        assert first["status"] == "ok", first
        assert first["trigger"] == "manual" and first["changed"] is True
        assert "hostname gpon-olt" in first["content"]
        assert "olterra-sim" not in first["content"]  # la clave de la OLT y la comunidad
        assert first["line_count"] == first["added"] > 3

        # Mientras hay uno en curso no se pide otro.
        env.client.post(f"/v1/olts/{olt['id']}/backups", headers=env.headers())
        again = env.client.post(f"/v1/olts/{olt['id']}/backups", headers=env.headers())
        assert again.status_code == 409
        run_plans(env, done)

        # 2) Sin cambios en la OLT: se registra, pero "sin cambios".
        listed = env.client.get(f"/v1/olts/{olt['id']}/backups", headers=env.headers()).json()
        assert [b["changed"] for b in listed] == [False, True]
        # No guarda otra copia, pero se abre igual (la del último con la misma huella).
        same = env.client.get(f"/v1/backups/{listed[0]['id']}", headers=env.headers()).json()
        assert same["content"] == first["content"]

        # 3) Cambia la OLT: el respaldo dice cuánto y la diferencia muestra qué.
        simulator.sim.config.hostname = "OLT_VIOTA"
        third = backup_now(env, olt["id"], done)
        assert third["changed"] is True and (third["added"], third["removed"]) == (1, 1)
        diff = env.client.get(f"/v1/backups/{third['id']}/diff", headers=env.headers()).json()
        assert diff["from_id"] == listed[0]["id"]
        assert {"kind": "-", "text": "hostname gpon-olt"} in diff["lines"]
        assert {"kind": "+", "text": "hostname OLT_VIOTA"} in diff["lines"]
        against = env.client.get(
            f"/v1/backups/{third['id']}/diff",
            params={"against": first["id"]},
            headers=env.headers(),
        ).json()
        assert against["from_id"] == first["id"]

        # La configuración queda solo en el respaldo, cifrada; el historial no la repite.
        async def stored(session: Any) -> tuple[list[bytes], list[Any]]:
            contents = (await session.execute(select(OltBackup.content))).scalars().all()
            results = (await session.execute(select(PlanRun.result))).scalars().all()
            return list(contents), list(results)

        contents, results = asyncio.run(_owner(env, stored))
        assert len([c for c in contents if c]) == 2  # solo los que cambiaron
        assert all(b"hostname" not in c for c in contents if c)
        outputs = [o for r in results if r for o in r.get("outputs", [])]
        saved = [o for o in outputs if o["key"] == "system.running_config"]
        assert saved and all(o["output"] is None and o["backup_id"] for o in saved)

        # 4) El reloj: nada mientras haya uno reciente; uno nuevo cuando venció. (Se cuentan los
        # de esta OLT: la base de pruebas tiene OLT de otras pruebas.)
        def count() -> int:
            url = f"/v1/olts/{olt['id']}/backups"
            return len(env.client.get(url, headers=env.headers()).json())

        before = count()
        env.client.portal.call(backups.tick, state)  # type: ignore[union-attr]
        assert count() == before

        async def age(session: Any) -> None:
            await session.execute(
                update(OltBackup).values(created_at=OltBackup.created_at - timedelta(days=2))
            )

        asyncio.run(_owner(env, age))
        env.client.portal.call(backups.tick, state)  # type: ignore[union-attr]
        assert count() == before + 1
        run_plans(env, done)
        latest = env.client.get(f"/v1/olts/{olt['id']}/backups", headers=env.headers()).json()[0]
        assert latest["trigger"] == "auto" and latest["requested_by"] == "olterra:respaldo"
        assert latest["status"] == "ok" and latest["changed"] is False

    # Otro ISP no ve nada.
    other = env.headers(env.key_b)
    assert env.client.get(f"/v1/backups/{first['id']}", headers=other).status_code == 404
    assert env.client.get(f"/v1/olts/{olt['id']}/backups", headers=other).json() == []


def test_unreachable_olt_backup_fails_once(env: Env) -> None:  # noqa: F811
    olt = create_olt(env)  # sin IP: no se puede ni pedir
    started = env.client.post(f"/v1/olts/{olt['id']}/backups", headers=env.headers())
    assert started.status_code == 202
    assert started.json()["status"] == "failed"
    assert "IP" in started.json()["error"]


def test_audit_log(env: Env) -> None:  # noqa: F811
    olt = create_olt(env)
    env.client.patch(f"/v1/olts/{olt['id']}", json={"model": "V1600G0-B"}, headers=env.headers())
    entries = env.client.get("/v1/audit", headers=env.headers()).json()
    assert [e["action"] for e in entries[:2]] == ["olt.update", "olt.create"]
    assert entries[0]["before"]["model"] is None and entries[0]["after"]["model"] == "V1600G0-B"

    only = env.client.get(
        "/v1/audit", params={"target_id": olt["id"], "action": "olt.upd"}, headers=env.headers()
    ).json()
    assert [e["action"] for e in only] == ["olt.update"]
    page = env.client.get(
        "/v1/audit", params={"before": entries[0]["id"], "limit": 1}, headers=env.headers()
    ).json()
    assert [e["id"] for e in page] == [entries[1]["id"]]
    # Ni el password de fábrica ni ninguna clave aparece.
    assert "factory-pass" not in str(entries)

    # Otro ISP no ve la bitácora de este; una llave sin el permiso no la lee.
    assert env.client.get("/v1/audit", headers=env.headers(env.key_b)).json() == []

    engine = create_engine(env.pg.owner_url)

    async def make() -> str:
        try:
            return await create_api_key(
                session_factory(engine), env.tenant_a, name="solo-lectura", scopes=["olt:read"]
            )
        finally:
            await engine.dispose()

    key = asyncio.run(make())
    assert env.client.get("/v1/audit", headers=env.headers(key)).status_code == 403
