"""API contra PostgreSQL real. La consulta a una OLT va de punta a punta:
API → plan sellado → ejecutor → SSH al simulador → resultado guardado → parser."""

from __future__ import annotations

import asyncio
import re
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
from tests.conftest import PgDatabase, b64key, ca_pem

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
        tunnel_sstp_ca=ca_pem(),
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


def test_olt_real_ip_cannot_be_a_tunnel_ip(env: Env) -> None:
    # La IP del router en el túnel (198.18.x) o una IP NAT (198.19.x) no son la de la OLT.
    for ip in ("198.18.1.1", "198.19.0.0"):
        response = env.client.post(
            "/v1/olts",
            json={"name": f"OLT-{ip[-3:]}", "username": "a", "password": "b", "real_ip": ip},
            headers=env.headers(),
        )
        assert response.status_code == 400
        assert "es una IP del túnel" in response.json()["detail"]


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


def test_query_size_is_capped(env: Env) -> None:
    olt = create_olt(env, real_ip="10.0.0.2")
    onus = [f"1:{n}" for n in range(1, 66)]
    response = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["onu.optical"], "onu": onus},
        headers=env.headers(),
    )
    assert response.status_code == 422
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
    assert run["source"] == "api" and run["files"] == []
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


def test_reconciliation_from_files(env: Env) -> None:
    files = [
        ("onus", ("onus.csv", "olt,onu,serial,pppoe\nOLT-1,1:1,VSOL0000A001,ana\n", "text/csv")),
        (
            "secrets",
            (
                "BNG-1.rsc",
                "/ppp secret\nadd name=ana password=Secreta123 service=pppoe\n"
                "add name=viejo password=x service=pppoe\n",
                "text/plain",
            ),
        ),
        (
            "customers",
            ("clientes.csv", "id;nombre;estado;usuario pppoe\nC1;Ana;activo;ana\n", "text/csv"),
        ),
    ]
    response = env.client.post("/v1/reconciliations/files", files=files, headers=env.headers())
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["source"] == "upload"
    assert run["files"] == [
        {"kind": "onus", "name": "onus.csv", "records": 1},
        {"kind": "secrets", "name": "BNG-1.rsc", "records": 2},
        {"kind": "customers", "name": "clientes.csv", "records": 1},
    ]
    # El secreto sin cliente sale con el router tomado del nombre del archivo.
    [orphan] = [f for f in run["findings"] if f["kind"] == "secreto_sin_cliente"]
    assert orphan["refs"]["pppoe"] == "viejo" and orphan["refs"]["router"] == "BNG-1"
    assert "Secreta123" not in response.text  # la clave del export no se guarda

    missing = env.client.post(
        "/v1/reconciliations/files",
        files=[("onus", ("onus.csv", "olt,pon\nA,1\n", "text/csv"))],
        headers=env.headers(),
    )
    assert missing.status_code == 400 and "serial" in missing.json()["detail"]
    empty = env.client.post(
        "/v1/reconciliations/files", data={"router_name": "BNG"}, headers=env.headers()
    )
    assert empty.status_code == 400


def test_reconciliation_demo_and_history(env: Env) -> None:
    demo = env.client.post("/v1/reconciliations/demo", headers=env.headers())
    assert demo.status_code == 201, demo.text
    assert demo.json()["source"] == "demo"
    assert len({f["kind"] for f in demo.json()["findings"]}) == 13

    history = env.client.get("/v1/reconciliations", headers=env.headers()).json()
    assert [run["id"] for run in history] == [demo.json()["id"]]
    assert "findings" not in history[0] and history[0]["counts"]["error"] > 0
    assert env.client.get("/v1/reconciliations", headers=env.headers(env.key_b)).json() == []


def test_me(env: Env) -> None:
    body = env.client.get("/v1/me", headers=env.headers()).json()
    assert body["tenant"]["id"] == str(env.tenant_a) and body["tenant"]["name"] == "ISP A"
    assert body["key_name"] == "a" and body["scopes"] == ["*"]
    assert env.client.get("/v1/me").status_code == 401


def test_olt_commands_and_plan_history(env: Env) -> None:
    olt = create_olt(env, real_ip="10.0.0.1")
    commands = env.client.get(f"/v1/olts/{olt['id']}/commands", headers=env.headers())
    assert commands.status_code == 200
    by_key = {c["key"]: c for c in commands.json()}
    assert by_key["system.version"]["scope"] == "olt" and by_key["system.version"]["parsed"]
    assert by_key["onu.list"]["scope"] == "pon"
    assert by_key["onu.optical"]["scope"] == "onu"
    assert "onu.authorize" not in by_key  # escribe en la OLT
    assert "profile.list" not in by_key  # pide parámetros que /queries no admite

    # Todo lo que ofrece el catálogo lo acepta /queries con sus parámetros.
    queued = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": list(by_key), "pon": [1], "onu": ["1:1"]},
        headers=env.headers(),
    )
    assert queued.status_code == 202, queued.text
    history = env.client.get(f"/v1/olts/{olt['id']}/plans", headers=env.headers()).json()
    assert [p["plan_id"] for p in history] == [queued.json()["plan_id"]]
    assert history[0]["commands"] == list(by_key) and history[0]["status"] == "queued"
    assert "result" not in history[0]

    for path in ("commands", "plans"):
        other = env.client.get(f"/v1/olts/{olt['id']}/{path}", headers=env.headers(env.key_b))
        assert other.status_code == 404


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


# --- Túnel SSTP (RouterOS v6) ----------------------------------------------------------


def test_sstp_router_for_routeros_v6(env: Env) -> None:
    created = env.client.post(
        "/v1/tunnel/routers",
        json={"name": "CORE_V6", "routeros_version": "6.49.18"},
        headers=env.headers(),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    router = body["router"]
    assert router["transport"] == "sstp" and router["wg_public_key"] is None
    assert router["ppp_user"].startswith("olterra-api-a-") and router["ppp_user"].endswith(
        "-CORE_V6"
    )
    assert "/interface sstp-client add" in body["isp_script"]
    assert "wireguard" not in body["isp_script"]
    match = re.search(r'password="([A-Za-z0-9]+)"', body["isp_script"])
    assert match is not None
    password = match.group(1)
    assert f'password="{password}" service=sstp' in body["hub_script"]  # la misma en los dos

    # Con una OLT detrás, el concentrador enruta su IP NAT por el túnel SSTP.
    olt = create_olt(env, router_id=router["id"], real_ip="192.168.8.200")
    rotated = env.client.post(
        f"/v1/tunnel/routers/{router['id']}/script", headers=env.headers()
    ).json()
    assert rotated["router"]["transport"] == "sstp"
    assert f'routes="{olt["nat_ip"]}/32"' in rotated["hub_script"]
    assert password not in rotated["isp_script"]  # rotar cambia la clave
    listed = env.client.get("/v1/tunnel/routers", headers=env.headers()).json()
    assert [r["transport"] for r in listed] == ["sstp"]


def test_router_changes_transport_when_rotating(env: Env) -> None:
    """Un router v6 dado de alta como v7 pasa a SSTP sin borrarlo ni cambiar su IP."""
    created = env.client.post(
        "/v1/tunnel/routers",
        json={"name": "CORE_VIOTA", "routeros_version": "7"},
        headers=env.headers(),
    ).json()
    router_id = created["router"]["id"]
    switched = env.client.post(
        f"/v1/tunnel/routers/{router_id}/script",
        json={"routeros_version": "6"},
        headers=env.headers(),
    )
    assert switched.status_code == 200, switched.text
    body = switched.json()
    assert body["router"]["transport"] == "sstp" and body["router"]["wg_public_key"] is None
    assert body["router"]["overlay_ip"] == created["router"]["overlay_ip"]
    assert '/interface wireguard peers remove [find where comment="olterra:' in body["hub_script"]

    back = env.client.post(
        f"/v1/tunnel/routers/{router_id}/script",
        json={"routeros_version": "7.16"},
        headers=env.headers(),
    ).json()
    assert back["router"]["transport"] == "wireguard" and back["router"]["ppp_user"] is None


def test_sstp_needs_the_concentrator_ca(env: Env) -> None:
    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    original = state.settings
    state.settings = original.model_copy(update={"tunnel_sstp_ca": None})
    try:
        response = env.client.post(
            "/v1/tunnel/routers",
            json={"name": "SIN-CA", "routeros_version": "6.49"},
            headers=env.headers(),
        )
        assert response.status_code == 503
        assert "OLTERRA_TUNNEL_SSTP_CA" in response.json()["detail"]
    finally:
        state.settings = original
