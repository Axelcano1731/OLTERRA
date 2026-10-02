"""API contra PostgreSQL real. La consulta a una OLT va de punta a punta:
API → plan sellado → ejecutor → SSH al simulador → resultado guardado → parser."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from olterra.admin import create_api_key, create_tenant
from olterra.api.app import create_app
from olterra.config import Settings
from olterra.db.models import AuditLog, Credential
from olterra.db.session import create_engine, session_factory
from olterra.devtools.demo_recon import demo_input
from olterra.devtools.vsol_sim import VsolSimulator
from olterra.executor.bus import MemoryBus
from olterra.executor.plan import Plan
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor
from olterra.security import apikeys
from olterra.security.sealed import generate_keypair
from olterra.security.vault import Vault
from olterra.tunnel.wireguard import generate_keypair as wg_keypair
from tests.conftest import PgDatabase, b64key

pytestmark = pytest.mark.postgres


@dataclass
class RecordingPublisher:
    plans: list[Plan] = field(default_factory=list)

    async def publish_plan(self, plan: Plan, group: str = "cloud") -> None:
        self.plans.append(plan)


@dataclass
class Env:
    client: TestClient
    publisher: RecordingPublisher
    executor_private_key: str
    tenant_a: UUID
    tenant_b: UUID
    key_a: str
    key_b: str
    pg: PgDatabase
    master_key: bytes

    def headers(self, key: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {key or self.key_a}"}


async def _make_tenants(pg: PgDatabase, master_key: bytes) -> tuple[UUID, UUID, str, str]:
    engine = create_engine(pg.owner_url)
    owner = session_factory(engine)
    vault = Vault.single(master_key)
    try:
        a = await create_tenant(owner, vault, slug=f"api-a-{uuid4().hex[:6]}", name="ISP A")
        b = await create_tenant(owner, vault, slug=f"api-b-{uuid4().hex[:6]}", name="ISP B")
        return (
            a.id,
            b.id,
            await create_api_key(owner, a.id, name="a"),
            await create_api_key(owner, b.id, name="b"),
        )
    finally:
        await engine.dispose()


@pytest.fixture
def env(pg: PgDatabase, master_key: bytes) -> Iterator[Env]:
    private, public = generate_keypair()
    _, hub_public = wg_keypair()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        database_url=pg.app_url,
        master_key=SecretStr(b64key(master_key)),
        executor_public_key=public,
        tunnel_hub_host="hub.olterra.co",
        tunnel_hub_public_key=hub_public,
    )
    a, b, key_a, key_b = asyncio.run(_make_tenants(pg, master_key))
    publisher = RecordingPublisher()
    with TestClient(create_app(settings, bus=publisher, connect_nats=False)) as client:
        yield Env(client, publisher, private, a, b, key_a, key_b, pg, master_key)


class SimulatorThread:
    """El simulador SSH en su propio hilo y loop (la API corre en el del TestClient)."""

    def __enter__(self) -> SimulatorThread:
        self.sim = VsolSimulator()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.port = asyncio.run_coroutine_threadsafe(self.sim.start(), self.loop).result(10)
        return self

    def __exit__(self, *exc: object) -> None:
        asyncio.run_coroutine_threadsafe(self.sim.stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def create_olt(env: Env, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": f"OLT-{uuid4().hex[:6]}",
        "username": "admin",
        "password": "olterra-sim",
    } | overrides
    response = env.client.post("/v1/olts", json=body, headers=env.headers())
    assert response.status_code == 201, response.text
    return response.json()


def test_health(env: Env) -> None:
    body = env.client.get("/health").json()
    assert body["database"] == "ok" and body["vault"] == "lista"


def test_authentication(env: Env) -> None:
    assert env.client.get("/v1/olts").status_code == 401
    assert env.client.get("/v1/olts", headers={"Authorization": "Bearer basura"}).status_code == 401
    assert env.client.get("/v1/olts", headers=env.headers()).status_code == 200
    # La llave de A con el tenant de B por delante: RLS no la encuentra.
    parsed = apikeys.parse(env.key_a)
    forged = env.key_a.replace(parsed.tenant_id.hex, env.tenant_b.hex, 1)
    assert env.client.get("/v1/olts", headers=env.headers(forged)).status_code == 401


def test_router_olt_nat_and_isolation(env: Env) -> None:
    created = env.client.post("/v1/tunnel/routers", json={"name": "BNG-1"}, headers=env.headers())
    assert created.status_code == 201, created.text
    router = created.json()
    assert router["router"]["overlay_ip"].startswith("198.18.")
    assert "endpoint-address=hub.olterra.co" in router["isp_script"]
    assert router["router"]["wg_public_key"] in router["hub_script"]

    olt = create_olt(
        env,
        router_id=router["router"]["id"],
        real_ip="192.168.8.200",
        latitude=4.45,
        longitude=-74.63,
    )
    assert olt["nat_ip"].startswith("198.19.")
    assert "password" not in olt

    rotated = env.client.post(
        f"/v1/tunnel/routers/{router['router']['id']}/script", headers=env.headers()
    ).json()
    assert (
        f"dst-address={olt['nat_ip']} action=dst-nat to-addresses=192.168.8.200"
        in rotated["isp_script"]
    )
    assert rotated["router"]["wg_public_key"] != router["router"]["wg_public_key"]

    # B no ve nada de A, ni por lista ni por id.
    assert env.client.get("/v1/olts", headers=env.headers(env.key_b)).json() == []
    assert (
        env.client.get(f"/v1/olts/{olt['id']}", headers=env.headers(env.key_b)).status_code == 404
    )

    # Bitácora y bóveda: quedó registro, y la clave no está en claro en la base.
    async def inspect() -> tuple[list[str], list[bytes]]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                actions = (
                    (
                        await session.execute(
                            select(AuditLog.action).where(AuditLog.tenant_id == env.tenant_a)
                        )
                    )
                    .scalars()
                    .all()
                )
                blobs = (
                    (
                        await session.execute(
                            select(Credential.ciphertext).where(
                                Credential.tenant_id == env.tenant_a
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            return list(actions), list(blobs)
        finally:
            await engine.dispose()

    actions, blobs = asyncio.run(inspect())
    assert {"tunnel.router.create", "olt.create", "tunnel.router.rotate_keys"} <= set(actions)
    assert blobs and all(b"olterra-sim" not in blob for blob in blobs)


def test_query_end_to_end(env: Env) -> None:
    with SimulatorThread() as simulator:
        olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port)
        response = env.client.post(
            f"/v1/olts/{olt['id']}/queries",
            json={
                "commands": ["system.version", "onu.list", "onu.optical"],
                "pon": [1],
                "onu": ["1:1"],
            },
            headers=env.headers(),
        )
        assert response.status_code == 202, response.text
        plan_id = response.json()["plan_id"]
        [plan] = env.publisher.plans
        assert str(plan.plan_id) == plan_id
        assert "olterra-sim" not in plan.model_dump_json()  # la clave va sellada

        async def execute() -> Any:
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
    app_state = env.client.app.state.olterra  # type: ignore[attr-defined]
    env.client.portal.call(app_state.store_result, result)  # type: ignore[union-attr]

    body = env.client.get(f"/v1/plans/{plan_id}", headers=env.headers()).json()
    assert body["status"] == "ok"
    outputs = {o["key"]: o for o in body["result"]["outputs"]}
    assert outputs["system.version"]["data"]["model"] == "V1600G1"
    assert [r["serial"] for r in outputs["onu.list"]["data"]] == [
        "VSOL0008D09C",
        "VSOL00A1B2C3",
        "HWTC1F2E3D4C",
    ]
    assert outputs["onu.optical"]["data"]["rx_dbm"] == -19.5
    # Otro tenant no ve el plan.
    assert env.client.get(f"/v1/plans/{plan_id}", headers=env.headers(env.key_b)).status_code == 404


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"commands": ["onu.authorize"], "pon": [1]}, "escribe"),
        ({"commands": ["no.existe"]}, "desconocido"),
        ({"commands": ["onu.list"]}, "PON"),
        ({"commands": ["onu.optical"], "pon": [1]}, "ONU"),
    ],
)
def test_query_validation(env: Env, body: dict[str, Any], detail: str) -> None:
    olt = create_olt(env, real_ip="10.0.0.1")
    response = env.client.post(f"/v1/olts/{olt['id']}/queries", json=body, headers=env.headers())
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert env.publisher.plans == []


def test_reconciliation_api(env: Env) -> None:
    data = demo_input()
    body = {
        "onus": [{**o.__dict__, "macs": list(o.macs)} for o in data.onus],
        "secrets": [s.__dict__ for s in data.secrets],
        "sessions": [s.__dict__ for s in data.sessions],
        "customers": [c.__dict__ for c in data.customers],
    }
    response = env.client.post("/v1/reconciliations", json=body, headers=env.headers())
    assert response.status_code == 201, response.text
    run = response.json()
    assert len({f["kind"] for f in run["findings"]}) == 13
    assert (
        env.client.get(f"/v1/reconciliations/{run['id']}", headers=env.headers()).status_code == 200
    )
    assert (
        env.client.get(
            f"/v1/reconciliations/{run['id']}", headers=env.headers(env.key_b)
        ).status_code
        == 404
    )


def test_scopes_are_enforced(env: Env) -> None:
    async def read_only_key() -> str:
        engine = create_engine(env.pg.owner_url)
        try:
            return await create_api_key(
                session_factory(engine), env.tenant_a, name="lectura", scopes=["olt:read"]
            )
        finally:
            await engine.dispose()

    key = asyncio.run(read_only_key())
    assert env.client.get("/v1/olts", headers=env.headers(key)).status_code == 200
    response = env.client.post(
        "/v1/olts", json={"name": "X", "username": "a", "password": "b"}, headers=env.headers(key)
    )
    assert response.status_code == 403
