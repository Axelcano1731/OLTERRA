"""API contra PostgreSQL real. La consulta a una OLT va de punta a punta:
API → plan sellado → ejecutor → SSH al simulador → resultado guardado → parser."""

from __future__ import annotations

import asyncio
import json
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
from olterra.db.models import AuditLog, Credential, Olt, ProvisionJob, TenantKey, TunnelRouter
from olterra.db.session import create_engine, session_factory
from olterra.devtools.demo_recon import demo_input
from olterra.devtools.vsol_sim import VsolSimulator
from olterra.executor.bus import MemoryBus
from olterra.executor.plan import Credential as SecretCredential
from olterra.executor.plan import Plan
from olterra.executor.runner import PlanRunner
from olterra.executor.worker import Executor
from olterra.security import apikeys
from olterra.security.sealed import generate_keypair, unseal
from olterra.security.vault import Vault
from olterra.tunnel.hub_api import HubApiError
from olterra.tunnel.wireguard import generate_keypair as wg_keypair
from tests.conftest import PgDatabase, b64key, ca_pem

pytestmark = pytest.mark.postgres

FACTORY_PASSWORD = "factory-pass-123"


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
        vsol_default_password=SecretStr(FACTORY_PASSWORD),
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


def gateway_password(script: str) -> str:
    match = re.search(r'/user add name=olterra group=olterra password="([A-Za-z0-9]+)"', script)
    assert match is not None
    return match.group(1)


def test_router_is_the_gateway_to_its_olts_and_tenants_are_isolated(env: Env) -> None:
    created = env.client.post("/v1/tunnel/routers", json={"name": "BNG-1"}, headers=env.headers())
    assert created.status_code == 201, created.text
    router = created.json()
    assert router["router"]["overlay_ip"].startswith("198.18.")
    assert router["router"]["gateway"] and router["router"]["ssh_port"] == 22
    assert "endpoint-address=hub.olterra.co" in router["isp_script"]
    assert router["router"]["wg_public_key"] in router["hub_script"]
    first_password = gateway_password(router["isp_script"])

    olt = create_olt(
        env,
        router_id=router["router"]["id"],
        real_ip="192.168.8.200",
        latitude=4.45,
        longitude=-74.63,
    )
    # Sin IP NAT: Olterra llega a la IP real desde el MikroTik. Una OLT nueva no cambia nada
    # del router ni del concentrador.
    assert olt["nat_ip"] is None
    assert "password" not in olt

    response = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["system.version"]},
        headers=env.headers(),
    )
    assert response.status_code == 202, response.text
    [plan] = env.publisher.plans
    assert plan.target.host == "192.168.8.200"
    assert plan.target.via is not None
    assert (plan.target.via.host, plan.target.via.port, plan.target.via.username) == (
        router["router"]["overlay_ip"],
        22,
        "olterra",
    )
    assert plan.credential is not None
    sealed = SecretCredential.model_validate_json(
        unseal(env.executor_private_key, plan.credential, plan.seal_context())
    )
    assert sealed.gateway_password is not None
    assert sealed.gateway_password.get_secret_value() == first_password
    assert first_password not in plan.model_dump_json()

    rotated = env.client.post(
        f"/v1/tunnel/routers/{router['router']['id']}/script",
        json={"ssh_port": 2222},
        headers=env.headers(),
    ).json()
    assert rotated["router"]["wg_public_key"] != router["router"]["wg_public_key"]
    assert rotated["router"]["ssh_port"] == 2222
    assert gateway_password(rotated["isp_script"]) != first_password  # rotar cambia la puerta
    assert "dst-nat" not in rotated["isp_script"]
    assert f"allowed-address={router['router']['overlay_ip']}/32 " in rotated["hub_script"]

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
    assert all(first_password.encode() not in blob for blob in blobs)


def test_olt_behind_a_router_with_the_old_script(env: Env) -> None:
    """Un router dado de alta antes de la puerta: sus OLT nuevas piden rotarlo una vez."""
    router = env.client.post(
        "/v1/tunnel/routers", json={"name": "BNG-VIEJO"}, headers=env.headers()
    ).json()["router"]
    olt = create_olt(env, router_id=router["id"], real_ip="172.17.1.2")

    async def forget_gateway() -> None:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session, session.begin():
                row = await session.get(TunnelRouter, UUID(router["id"]))
                assert row is not None
                row.gateway_credential_id = None
        finally:
            await engine.dispose()

    asyncio.run(forget_gateway())
    listed = env.client.get("/v1/tunnel/routers", headers=env.headers()).json()
    assert [r["gateway"] for r in listed] == [False]
    response = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["system.version"]},
        headers=env.headers(),
    )
    assert response.status_code == 409
    assert "BNG-VIEJO tiene el script viejo" in response.json()["detail"]
    assert env.publisher.plans == []


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
    # La OLT respondió: deja de estar "pending" y queda la hora en que se la vio.
    seen = env.client.get(f"/v1/olts/{plan.olt_id}", headers=env.headers()).json()
    assert seen["status"] == "online" and seen["last_seen_at"] is not None
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


def test_delete_olt_removes_its_credential_and_leaves_a_trace(env: Env) -> None:
    olt = create_olt(env, real_ip="10.0.0.20")
    # Otro ISP no puede borrarla (ni saber que existe).
    other = env.client.delete(f"/v1/olts/{olt['id']}", headers=env.headers(env.key_b))
    assert other.status_code == 404
    assert env.client.get(f"/v1/olts/{olt['id']}", headers=env.headers()).status_code == 200

    async def credentials_and_audit() -> tuple[int, list[dict[str, Any]]]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                count = (
                    await session.execute(
                        select(Credential.id).where(Credential.tenant_id == env.tenant_a)
                    )
                ).all()
                audits = (
                    (
                        await session.execute(
                            select(AuditLog.before).where(
                                AuditLog.tenant_id == env.tenant_a,
                                AuditLog.action == "olt.delete",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            return len(count), list(audits)
        finally:
            await engine.dispose()

    before_credentials, _ = asyncio.run(credentials_and_audit())
    deleted = env.client.delete(f"/v1/olts/{olt['id']}", headers=env.headers())
    assert deleted.status_code == 204 and deleted.content == b""
    assert env.client.get(f"/v1/olts/{olt['id']}", headers=env.headers()).status_code == 404
    assert env.client.delete(f"/v1/olts/{olt['id']}", headers=env.headers()).status_code == 404

    after_credentials, audits = asyncio.run(credentials_and_audit())
    assert after_credentials == before_credentials - 1  # su credencial cifrada también se fue
    [trace] = audits
    assert trace["name"] == olt["name"] and trace["real_ip"] == "10.0.0.20"
    assert "password" not in json.dumps(trace)


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

    # Con una OLT detrás, el concentrador sigue igual: solo la IP del router (la puerta).
    create_olt(env, router_id=router["id"], real_ip="192.168.8.200")
    rotated = env.client.post(
        f"/v1/tunnel/routers/{router['id']}/script", headers=env.headers()
    ).json()
    assert rotated["router"]["transport"] == "sstp"
    assert "routes=" not in rotated["hub_script"]
    assert "/user add name=olterra group=olterra" in rotated["isp_script"]
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


def test_update_olt_and_its_credentials(env: Env) -> None:
    olt = create_olt(env, real_ip="10.0.0.9", model="V1600G1B")
    response = env.client.patch(
        f"/v1/olts/{olt['id']}",
        json={
            "real_ip": "10.72.111.2",
            "model": "V1600G0-B",
            "firmware": "V1.4.8R",
            "enable_password": "enable-lab",
        },
        headers=env.headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["real_ip"], body["model"], body["firmware"]) == (
        "10.72.111.2",
        "V1600G0-B",
        "V1.4.8R",
    )

    async def stored() -> tuple[SecretCredential, bytes, list[dict[str, Any]]]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                row = await session.get(Olt, UUID(olt["id"]))
                assert row is not None and row.credential_id is not None
                secret = await session.get(Credential, row.credential_id)
                assert secret is not None
                wrapped = (
                    await session.execute(
                        select(TenantKey.wrapped_dek).where(TenantKey.tenant_id == env.tenant_a)
                    )
                ).scalar_one()
                audits = (
                    (
                        await session.execute(
                            select(AuditLog.after).where(
                                AuditLog.tenant_id == env.tenant_a,
                                AuditLog.action == "olt.update",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            dek = Vault.single(env.master_key).unwrap_tenant_key(env.tenant_a, wrapped)
            plain = Vault.decrypt(env.tenant_a, dek, f"credential:{secret.id}", secret.ciphertext)
            return SecretCredential.model_validate_json(plain), secret.ciphertext, list(audits)
        finally:
            await engine.dispose()

    credential, ciphertext, audits = asyncio.run(stored())
    assert credential.enable_password is not None
    assert credential.enable_password.get_secret_value() == "enable-lab"
    assert credential.password is not None
    assert credential.password.get_secret_value() == "olterra-sim"  # la que no vino, igual
    assert b"enable-lab" not in ciphertext
    [after] = audits
    assert after["credenciales"] == ["enable_password"]
    assert "enable-lab" not in json.dumps(after)  # la bitácora no guarda claves

    # Una cadena vacía quita la clave de enable.
    cleared = env.client.patch(
        f"/v1/olts/{olt['id']}", json={"enable_password": ""}, headers=env.headers()
    )
    assert cleared.status_code == 200
    assert asyncio.run(stored())[0].enable_password is None

    tunnel_ip = env.client.patch(
        f"/v1/olts/{olt['id']}", json={"real_ip": "198.18.1.1"}, headers=env.headers()
    )
    assert tunnel_ip.status_code == 400
    other = env.client.patch(
        f"/v1/olts/{olt['id']}", json={"model": "X"}, headers=env.headers(env.key_b)
    )
    assert other.status_code == 404


def test_new_olt_without_password_gets_the_factory_one(env: Env) -> None:
    defaults = env.client.get("/v1/olts/defaults", headers=env.headers())
    assert defaults.status_code == 200
    assert defaults.json() | {"drivers": []} == {
        "username": "admin",
        "password_configured": True,
        "drivers": [],
    }
    assert FACTORY_PASSWORD not in defaults.text

    # Sin usuario ni clave: los de fábrica; la clave de enable es la que pone el cliente.
    response = env.client.post(
        "/v1/olts",
        json={"name": "OLT-FABRICA", "enable_password": "enable-cliente"},
        headers=env.headers(),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["used_default_credentials"] is True
    assert FACTORY_PASSWORD not in response.text and "enable-cliente" not in response.text

    async def stored() -> tuple[SecretCredential, list[dict[str, Any]]]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                row = await session.get(Olt, UUID(created["id"]))
                assert row is not None and row.credential_id is not None
                secret = await session.get(Credential, row.credential_id)
                assert secret is not None
                wrapped = (
                    await session.execute(
                        select(TenantKey.wrapped_dek).where(TenantKey.tenant_id == env.tenant_a)
                    )
                ).scalar_one()
                audits = (
                    (
                        await session.execute(
                            select(AuditLog.after).where(
                                AuditLog.tenant_id == env.tenant_a,
                                AuditLog.action == "olt.create",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            dek = Vault.single(env.master_key).unwrap_tenant_key(env.tenant_a, wrapped)
            plain = Vault.decrypt(env.tenant_a, dek, f"credential:{secret.id}", secret.ciphertext)
            return SecretCredential.model_validate_json(plain), list(audits)
        finally:
            await engine.dispose()

    credential, audits = asyncio.run(stored())
    assert credential.username == "admin"
    assert credential.password.get_secret_value() == FACTORY_PASSWORD
    assert credential.enable_password is not None
    assert credential.enable_password.get_secret_value() == "enable-cliente"
    assert any(a.get("used_default_credentials") is True for a in audits)
    assert FACTORY_PASSWORD not in json.dumps(audits)

    # Con clave propia no se toca la de fábrica, y sin usuario no hay a quién aplicarla.
    own = create_olt(env, username="otro", password="la-del-cliente")
    assert own["used_default_credentials"] is False
    no_user = env.client.post(
        "/v1/olts", json={"name": "OLT-SIN-USER", "password": "x"}, headers=env.headers()
    )
    assert no_user.status_code == 400


PROVISION_TEMPLATE = {
    "auth_profile": "default",
    "onu_profile": "default",
    "tconts": [{"id": 1, "name": "INTERNET", "dba": "default1"}],
    "gemports": [{"id": 1, "tcont": 1, "name": "INTERNET", "limit_down": "default"}],
    "services": [{"name": "ser_1", "gemport": 1, "vlan": 111}],
    "service_ports": [{"id": 1, "gemport": 1, "user_vlan": 111, "vlan": 111, "cos": 0}],
    "wan": {"vlan": 111, "binds": ["lan1", "ssid1"]},
    "wifi": {"ssid_index": 1},
}


def test_provision_templates_and_authorize_without_leaking_keys(env: Env) -> None:
    olt = create_olt(env, real_ip="10.0.0.9", model="V1600G0-B", firmware="V1.4.8R")
    created = env.client.post(
        "/v1/provision-templates",
        json={"name": "Hogar VLAN 111", "body": PROVISION_TEMPLATE},
        headers=env.headers(),
    )
    assert created.status_code == 201, created.text
    template = created.json()
    again = env.client.post(
        "/v1/provision-templates",
        json={"name": "Hogar VLAN 111", "body": PROVISION_TEMPLATE},
        headers=env.headers(),
    )
    assert again.status_code == 409
    # Otro ISP no la ve ni la puede usar.
    assert env.client.get("/v1/provision-templates", headers=env.headers(env.key_b)).json() == []

    alta = {
        "template_id": template["id"],
        "pon": 1,
        "serial": "GPON00AABBCC",
        "customer": "Cliente Nuevo",
        "pppoe_user": "cliente.nuevo",
        "pppoe_password": "Ppp#Secreta-77",
        "wifi_name": "Casa Nueva",
        "wifi_key": "Wifi#Secreta-77",
    }
    url = f"/v1/olts/{olt['id']}/onus/authorize"
    # Sin captura de laboratorio, la API se niega de entrada y dice qué falta validar.
    refused = env.client.post(url, json=alta, headers=env.headers())
    assert (
        refused.status_code == 400 and "onu.tcont" in refused.text
    )  # el alta ya se probó, el servicio no
    assert env.publisher.plans == []

    env.client.app.state.olterra.settings.allow_unverified_writes = True  # type: ignore[attr-defined]
    try:
        response = env.client.post(url, json=alta, headers=env.headers())
    finally:
        env.client.app.state.olterra.settings.allow_unverified_writes = False  # type: ignore[attr-defined]
    assert response.status_code == 202, response.text
    job = response.json()
    assert "onu.tcont" in job["unverified"]
    assert job["status"] == "running" and job["steps"][0]["status"] == "running"
    assert job["description"] == "Cliente_Nuevo" and job["wifi_ssid"] == "Casa_Nueva"
    # El primer paso solo lee (qué posiciones están libres); escribir viene después.
    [plan] = env.publisher.plans
    assert plan.access == "read"
    for secret in ("Ppp#Secreta-77", "Wifi#Secreta-77"):
        assert secret not in plan.model_dump_json()
        assert secret not in response.text

    async def stored() -> tuple[list[Any], bytes | None]:
        engine = create_engine(env.pg.owner_url)
        try:
            async with session_factory(engine)() as session:
                audits = (
                    (
                        await session.execute(
                            select(AuditLog.after).where(AuditLog.action == "onu.authorize")
                        )
                    )
                    .scalars()
                    .all()
                )
                row = await session.get(ProvisionJob, UUID(job["id"]))
                return list(audits), row.secrets if row else None
        finally:
            await engine.dispose()

    audits, sealed = asyncio.run(stored())
    assert "Ppp#Secreta-77" not in json.dumps(audits) and "Wifi#Secreta-77" not in json.dumps(
        audits
    )
    # Mientras el trabajo las necesita, las claves van cifradas con la llave del ISP.
    assert sealed is not None and b"Ppp#Secreta-77" not in sealed
    [after] = audits
    assert after["serial"] == "GPON00AABBCC" and after["pppoe_user"] == "cliente.nuevo"

    other = env.client.post(url, json=alta, headers=env.headers(env.key_b))
    assert other.status_code == 404
    bad = env.client.post(url, json=alta | {"wifi_key": "con?pregunta"}, headers=env.headers())
    assert bad.status_code == 422


def test_olt_over_telnet(env: Env) -> None:
    """Una OLT que no acepta a los usuarios de la CLI por SSH (Tocaima, V1.1.9R)."""
    router = env.client.post(
        "/v1/tunnel/routers", json={"name": "BNG-TEL"}, headers=env.headers()
    ).json()
    olt = create_olt(
        env, router_id=router["router"]["id"], real_ip="172.17.0.2", cli_protocol="telnet"
    )
    assert (olt["cli_protocol"], olt["telnet_port"]) == ("telnet", 23)

    # La consulta sale con el protocolo de la OLT, desde el MikroTik hacia su IP real.
    response = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["system.version"]},
        headers=env.headers(),
    )
    assert response.status_code == 202, response.text
    [plan] = env.publisher.plans
    assert (plan.target.cli_protocol, plan.target.telnet_port) == ("telnet", 23)
    assert plan.target.host == "172.17.0.2" and plan.target.via is not None

    # Y se puede volver a SSH desde Editar.
    edited = env.client.patch(
        f"/v1/olts/{olt['id']}", json={"cli_protocol": "ssh"}, headers=env.headers()
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["cli_protocol"] == "ssh"
    assert (
        env.client.patch(
            f"/v1/olts/{olt['id']}", json={"cli_protocol": "http"}, headers=env.headers()
        ).status_code
        == 422
    )


def test_olt_of_another_brand(env: Env) -> None:
    """HSGQ y BCD (Tocaima): solo lectura, con sus propios comandos."""
    defaults = env.client.get("/v1/olts/defaults", headers=env.headers()).json()
    brands = {d["key"]: d["writes"] for d in defaults["drivers"]}
    assert brands == {"vsol-gpon": True, "hsgq-gpon": False, "bcd-gpon": False}

    # Las claves de fábrica del servidor son de VSOL: otra marca necesita las suyas.
    no_password = env.client.post(
        "/v1/olts", json={"name": "OLT-HSGQ-0", "driver": "hsgq-gpon"}, headers=env.headers()
    )
    assert no_password.status_code == 400
    unknown = env.client.post(
        "/v1/olts",
        json={"name": "OLT-X", "driver": "huawei", "username": "a", "password": "b"},
        headers=env.headers(),
    )
    assert unknown.status_code == 422

    olt = create_olt(env, driver="hsgq-gpon", real_ip="172.17.1.2", model="HSGQ-G008")
    assert olt["driver"] == "hsgq-gpon"
    commands = env.client.get(f"/v1/olts/{olt['id']}/commands", headers=env.headers()).json()
    by_key = {c["key"]: c for c in commands}
    assert by_key["onu.state"]["command"] == "show ont-info {pon} all"
    assert "onu.optical" not in by_key

    response = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["onu.state"], "pon": [2]},
        headers=env.headers(),
    )
    assert response.status_code == 202, response.text
    [plan] = env.publisher.plans
    assert "show ont-info 2 all" in [step.command for step in plan.steps]

    # Un comando que esta marca no tiene: 400 que lo dice, no un 500.
    missing = env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["onu.optical"], "onu": ["1:1"]},
        headers=env.headers(),
    )
    assert missing.status_code == 400

    edited = env.client.patch(
        f"/v1/olts/{olt['id']}", json={"driver": "bcd-gpon"}, headers=env.headers()
    )
    assert edited.status_code == 200 and edited.json()["driver"] == "bcd-gpon"
    bad = env.client.patch(f"/v1/olts/{olt['id']}", json={"driver": "zte"}, headers=env.headers())
    assert bad.status_code == 422


def test_router_is_registered_in_the_hub_by_itself(env: Env) -> None:
    """Con la API del concentrador configurada, nadie pega el bloque a mano."""
    registered: list[Any] = []

    async def registrar(peer: Any) -> None:
        registered.append(peer)

    state = env.client.app.state.olterra  # type: ignore[attr-defined]
    state.hub_registrar = registrar
    try:
        body = env.client.post(
            "/v1/tunnel/routers", json={"name": "BNG-AUTO"}, headers=env.headers()
        ).json()
        assert body["hub_registered"] and body["hub_error"] is None
        [peer] = registered
        assert (peer.router, str(peer.address)) == ("BNG-AUTO", body["router"]["overlay_ip"])

        async def broken(peer: Any) -> None:
            raise HubApiError("No se pudo dar de alta el router en el concentrador: timeout")

        state.hub_registrar = broken
        rotated = env.client.post(
            f"/v1/tunnel/routers/{body['router']['id']}/script", headers=env.headers()
        ).json()
        assert not rotated["hub_registered"]
        assert "concentrador" in rotated["hub_error"]
        assert rotated["router"]["wg_public_key"] in rotated["hub_script"]  # queda el manual
    finally:
        state.hub_registrar = None


def test_olt_says_which_mikrotik_it_goes_through(env: Env) -> None:
    router = env.client.post(
        "/v1/tunnel/routers", json={"name": "BNG-INFO"}, headers=env.headers()
    ).json()["router"]
    behind = create_olt(env, router_id=router["id"], real_ip="172.16.0.2")
    direct = create_olt(env, real_ip="10.0.0.40")
    assert (behind["router_name"], behind["router_gateway"]) == ("BNG-INFO", True)
    assert (direct["router_name"], direct["router_gateway"]) == (None, None)
    listed = {o["id"]: o for o in env.client.get("/v1/olts", headers=env.headers()).json()}
    assert listed[behind["id"]]["router_gateway"] is True


def test_router_ssh_port_changes_without_rotating(env: Env) -> None:
    """Viotá tiene el SSH en el 2222: se corrige en Olterra sin volver a importar el script."""
    created = env.client.post(
        "/v1/tunnel/routers", json={"name": "CORE_PUERTO"}, headers=env.headers()
    ).json()
    router = created["router"]
    olt = create_olt(env, router_id=router["id"], real_ip="10.72.111.2")
    response = env.client.patch(
        f"/v1/tunnel/routers/{router['id']}", json={"ssh_port": 2222}, headers=env.headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["ssh_port"] == 2222
    assert response.json()["wg_public_key"] == router["wg_public_key"]  # no rotó
    env.client.post(
        f"/v1/olts/{olt['id']}/queries",
        json={"commands": ["system.version"]},
        headers=env.headers(),
    )
    [plan] = env.publisher.plans
    assert plan.target.via is not None and plan.target.via.port == 2222
    bad = env.client.patch(
        f"/v1/tunnel/routers/{router['id']}", json={"ssh_port": 0}, headers=env.headers()
    )
    assert bad.status_code == 422
    other = env.client.patch(
        f"/v1/tunnel/routers/{router['id']}", json={"ssh_port": 22}, headers=env.headers(env.key_b)
    )
    assert other.status_code == 404


def test_rename_delete_and_profiles_on_the_three_brands(env: Env) -> None:
    """Probado en producción el 2026-10-31: renombrar, eliminar y (HSGQ) cambiar perfiles."""
    hsgq = create_olt(env, driver="hsgq-gpon", real_ip="10.9.0.1", model="HSGQ-G008")
    bcd = create_olt(env, driver="bcd-gpon", real_ip="10.9.0.2", model="BCD-1508GS")
    vsol = create_olt(env, real_ip="10.9.0.3")

    def commands(response: Any) -> list[str]:
        assert response.status_code == 202, response.text
        assert response.json()["unverified"] == []
        plan = env.publisher.plans[-1]
        return [s.command for s in plan.steps]

    renamed = env.client.post(
        f"/v1/olts/{hsgq['id']}/onus/description",
        json={"pon": 3, "onu": 28, "description": "Cristina Gálvis"},
        headers=env.headers(),
    )
    assert 'ont setting 27 name "Cristina_Galvis"' in commands(renamed)  # ONU desde 0
    assert "save" in commands(renamed)
    renamed = env.client.post(
        f"/v1/olts/{bcd['id']}/onus/description",
        json={"pon": 1, "onu": 4, "description": "Cristina Gálvis"},
        headers=env.headers(),
    )
    assert "ont description 1 4 Cristina_Galvis" in commands(renamed)
    renamed = env.client.post(
        f"/v1/olts/{vsol['id']}/onus/description",
        json={"pon": 1, "onu": 8, "description": "Cristina Gálvis"},
        headers=env.headers(),
    )
    assert "onu 8 desc Cristina_Galvis" in commands(renamed)

    for olt, expected in ((hsgq, "ont delete 27"), (bcd, "ont delete 1 4"), (vsol, "no onu 8")):
        deleted = env.client.post(
            f"/v1/olts/{olt['id']}/onus/delete",
            json={
                "pon": 3 if olt is hsgq else 1,
                "onu": 28 if olt is hsgq else (4 if olt is bcd else 8),
            },
            headers=env.headers(),
        )
        assert expected in commands(deleted)

    profiles = env.client.post(
        f"/v1/olts/{hsgq['id']}/onus/profiles",
        json={"pon": 3, "onu": 28, "line_profile": 0, "srv_profile": 0},
        headers=env.headers(),
    )
    assert "ont modify 27 ont-lineprofile-id 0 ont-srvprofile-id 0" in commands(profiles)
    unsupported = env.client.post(
        f"/v1/olts/{bcd['id']}/onus/profiles",
        json={"pon": 1, "onu": 4, "line_profile": 10, "srv_profile": 11},
        headers=env.headers(),
    )
    assert unsupported.status_code == 400
