"""Mapa FTTH por la API: cajas con puertos, cables, postes, OLT ubicada y factibilidad."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from olterra.db.models import OnuStatus
from olterra.db.session import create_engine, session_factory, tenant_session
from tests.integration.test_api import Env, create_olt, env  # noqa: F401

pytestmark = pytest.mark.postgres

# Plaza de Viotá (aprox.): 0,001° de latitud son unos 111 m.
LAT, LNG = 4.4386, -74.5226


def box(env: Env, name: str, lat: float, lng: float, **extra: Any) -> dict[str, Any]:  # noqa: F811
    response = env.client.post(
        "/v1/map/boxes",
        json={"name": name, "lat": lat, "lng": lng, **extra},
        headers=env.headers(),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _onu_down(env: Env, olt_id: str) -> None:  # noqa: F811
    engine = create_engine(env.pg.app_url)
    try:
        async with tenant_session(session_factory(engine), env.tenant_a) as session:
            session.add(
                OnuStatus(
                    tenant_id=env.tenant_a,
                    olt_id=UUID(olt_id),
                    pon=1,
                    onu=1,
                    online=False,
                    phase="LOS",
                    description="ANA_PEREZ",
                )
            )
    finally:
        await engine.dispose()


def test_ftth_map(env: Env) -> None:  # noqa: F811
    h = env.headers()
    olt = create_olt(env, real_ip="10.72.111.2")
    placed = env.client.put(
        f"/v1/map/olts/{olt['id']}/location", json={"lat": LAT, "lng": LNG}, headers=h
    )
    assert placed.status_code == 200

    nap = box(env, "NAP-01", LAT + 0.001, LNG, ports=8, splitter="1:8", olt_id=olt["id"], pon=1)
    far = box(env, "NAP-LEJOS", LAT + 0.02, LNG, ports=16)
    assert nap["used"] == 0 and nap["ports"] == 8

    # Cliente en el puerto 1; si se pone en el 3, se mueve (una ONU va en un solo puerto).
    url = f"/v1/map/boxes/{nap['id']}/ports"
    one = {"olt_id": olt["id"], "pon": 1, "onu": 1}
    assert env.client.put(f"{url}/1", json=one, headers=h).status_code == 200
    moved = env.client.put(f"{url}/3", json=one, headers=h).json()
    assert [p["port"] for p in moved["port_list"]] == [3] and moved["used"] == 1
    assert env.client.put(f"{url}/9", json=one, headers=h).status_code == 400  # tiene 8
    too_small = env.client.patch(f"/v1/map/boxes/{nap['id']}", json={"ports": 2}, headers=h)
    assert too_small.status_code == 400 and "puerto 3" in too_small.json()["detail"]

    # El monitoreo dice que ese cliente está sin luz: la caja lo muestra.
    asyncio.run(_onu_down(env, olt["id"]))
    detail = env.client.get(f"/v1/map/boxes/{nap['id']}", headers=h).json()
    assert detail["down"] == 1
    assert detail["port_list"][0]["description"] == "ANA_PEREZ"
    assert detail["port_list"][0]["phase_label"] == "Sin luz (fibra)"

    cable = env.client.post(
        "/v1/map/cables",
        json={"name": "Troncal 1", "fibers": 24, "coords": [[LNG, LAT], [LNG, LAT + 0.001]]},
        headers=h,
    ).json()
    assert 105 < cable["length_m"] < 115
    pole = env.client.post(
        "/v1/map/poles",
        json={"lat": LAT + 0.0005, "lng": LNG, "owner": "Enel"},
        headers=h,
    ).json()

    everything = env.client.get("/v1/map", headers=h).json()
    assert [o["lat"] for o in everything["olts"]] == [pytest.approx(LAT)]
    assert {b["name"] for b in everything["boxes"]} == {"NAP-01", "NAP-LEJOS"}
    assert everything["cables"][0]["coords"][1] == [pytest.approx(LNG), pytest.approx(LAT + 0.001)]
    assert everything["poles"][0]["owner"] == "Enel"

    # Factibilidad: cerca solo la NAP-01 (7 libres); con más radio, también la lejana.
    near = env.client.get("/v1/map/feasibility", params={"lat": LAT, "lng": LNG}, headers=h).json()
    assert [(o["box"]["name"], o["free_ports"]) for o in near["options"]] == [("NAP-01", 7)]
    assert 100 < near["options"][0]["distance_m"] < 120
    wide = env.client.get(
        "/v1/map/feasibility", params={"lat": LAT, "lng": LNG, "radius_m": 5000}, headers=h
    ).json()
    assert [o["box"]["name"] for o in wide["options"]] == ["NAP-01", "NAP-LEJOS"]

    # Bitácora de cada cambio.
    actions = [e["action"] for e in env.client.get("/v1/audit", headers=h).json()]
    for action in ("map.olt.place", "map.box.create", "map.port.assign", "map.cable.create"):
        assert action in actions

    # Borrar la caja libera sus puertos; otro ISP no ve nada.
    other = env.headers(env.key_b)
    assert env.client.get(f"/v1/map/boxes/{nap['id']}", headers=other).status_code == 404
    assert env.client.get("/v1/map", headers=other).json()["boxes"] == []
    assert env.client.delete(f"/v1/map/poles/{pole['id']}", headers=other).status_code == 404
    assert env.client.delete(f"/v1/map/boxes/{nap['id']}", headers=h).status_code == 204
    assert (
        env.client.put(f"/v1/map/boxes/{far['id']}/ports/1", json=one, headers=h).json()["used"]
        == 1
    )
