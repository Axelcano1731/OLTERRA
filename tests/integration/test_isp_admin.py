"""El admin del ISP: sus usuarios, el nombre del ISP y el nombre de cada cliente en el panel."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from tests.integration.test_api import (  # noqa: F401 - fixtures y ayudas compartidas
    Env,
    SimulatorThread,
    create_olt,
    env,
)
from tests.integration.test_auth import INITIAL, NEW, bearer, login, make_user, unique
from tests.integration.test_monitoring import alarms, poll

pytestmark = pytest.mark.postgres


def admin_token(env: Env) -> tuple[str, str]:  # noqa: F811
    """Un admin del ISP A con la contraseña ya cambiada: (usuario, token)."""
    name = unique("admin")
    make_user(env, name, role="admin")
    token = login(env, name, INITIAL).json()["token"]
    changed = env.client.post(
        "/v1/auth/password",
        json={"current_password": INITIAL, "new_password": NEW},
        headers=bearer(token),
    )
    assert changed.status_code == 204, changed.text
    return name, token


def test_admin_creates_and_manages_users_of_its_isp(env: Env) -> None:  # noqa: F811
    _, token = admin_token(env)
    admin = bearer(token)
    username = unique("tecnico")
    created = env.client.post(
        "/v1/users",
        json={"username": username, "display_name": "Juan Técnico", "role": "tecnico"},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    password = body["initial_password"]
    assert len(password) == 12 and body["user"]["must_change_password"]
    assert body["user"]["role"] == "tecnico"

    # Entra con la contraseña inicial y tiene que cambiarla.
    first = login(env, username, password).json()
    assert first["must_change_password"] is True
    tech_token = first["token"]
    env.client.post(
        "/v1/auth/password",
        json={"current_password": password, "new_password": "Tecnico-Fibra-2026"},
        headers=bearer(tech_token),
    )
    # Un técnico no administra usuarios.
    assert env.client.get("/v1/users", headers=bearer(tech_token)).status_code == 403

    # El admin lo ve; el otro ISP no.
    listed = {u["username"] for u in env.client.get("/v1/users", headers=admin).json()}
    assert username in listed
    other = {
        u["username"] for u in env.client.get("/v1/users", headers=env.headers(env.key_b)).json()
    }
    assert username not in other

    # El nombre de usuario es único en toda la plataforma (también contra el otro ISP).
    taken = unique("ocupado")
    make_user(env, taken, tenant="b")
    clash = env.client.post("/v1/users", json={"username": taken}, headers=admin)
    assert clash.status_code == 409 and "ya existe" in clash.text

    # Deshabilitado: no entra y su sesión abierta se cierra.
    user_id = body["user"]["id"]
    disabled = env.client.patch(f"/v1/users/{user_id}", json={"disabled": True}, headers=admin)
    assert disabled.status_code == 200 and disabled.json()["disabled"] is True
    assert login(env, username, "Tecnico-Fibra-2026").status_code == 401
    assert env.client.get("/v1/me", headers=bearer(tech_token)).status_code == 401

    # Se habilita otra vez y se le restablece la contraseña: una inicial nueva, a cambiar.
    env.client.patch(
        f"/v1/users/{user_id}", json={"disabled": False, "role": "lectura"}, headers=admin
    )
    reset = env.client.post(f"/v1/users/{user_id}/password", headers=admin)
    assert reset.status_code == 200
    again = login(env, username, reset.json()["initial_password"]).json()
    assert again["must_change_password"] is True
    assert reset.json()["user"]["role"] == "lectura"

    # Otro ISP no puede tocarlo.
    foreign = env.client.patch(
        f"/v1/users/{user_id}", json={"role": "admin"}, headers=env.headers(env.key_b)
    )
    assert foreign.status_code == 404


def test_admin_cannot_demote_or_disable_itself(env: Env) -> None:  # noqa: F811
    name, token = admin_token(env)
    me = next(
        u
        for u in env.client.get("/v1/users", headers=bearer(token)).json()
        if u["username"] == name
    )
    for change in ({"role": "tecnico"}, {"disabled": True}):
        response = env.client.patch(f"/v1/users/{me['id']}", json=change, headers=bearer(token))
        assert response.status_code == 400 and "otro admin" in response.text


def test_isp_admin_renames_its_isp(env: Env) -> None:  # noqa: F811
    renamed = env.client.patch(
        "/v1/tenant", json={"name": "  Colombianet  "}, headers=env.headers()
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Colombianet"
    me = env.client.get("/v1/me", headers=env.headers()).json()
    assert me["tenant"]["name"] == "Colombianet"
    # El slug no cambia (va en los scripts de los MikroTik) y el otro ISP sigue igual.
    assert me["tenant"]["slug"] == renamed.json()["slug"]
    other = env.client.get("/v1/me", headers=env.headers(env.key_b)).json()
    assert other["tenant"]["name"] != "Colombianet"


def test_customer_name_from_the_panel(env: Env) -> None:  # noqa: F811
    done: set[UUID] = set()
    with SimulatorThread() as simulator:
        olt = create_olt(env, real_ip="127.0.0.1", ssh_port=simulator.port)
        poll(env, olt["id"], done)
        url = f"/v1/monitoring/onus/{olt['id']}/1/3/name"
        named = env.client.put(
            url, json={"customer_name": "  Cristina   Galvis "}, headers=env.headers()
        )
        assert named.status_code == 200, named.text
        assert named.json()["customer_name"] == "Cristina Galvis"

        # El sondeo no lo pisa, la búsqueda lo encuentra y la alarma lo usa.
        poll(env, olt["id"], done)
        found = env.client.get(
            "/v1/monitoring/onus", params={"q": "cristina"}, headers=env.headers()
        ).json()
        assert [(o["pon"], o["onu"], o["customer_name"]) for o in found] == [
            (1, 3, "Cristina Galvis")
        ]
        offline: dict[str, Any] = alarms(env, olt["id"])[(1, 3, "onu_offline")]
        assert offline["message"].startswith("Cristina Galvis:")

        # Otro ISP no lo ve ni lo cambia.
        assert (
            env.client.put(
                url, json={"customer_name": "x"}, headers=env.headers(env.key_b)
            ).status_code
            == 404
        )

        # En esa posición aparece otra ONU (otro serial): el nombre era del cliente anterior.
        onu = next(o for o in simulator.sim.config.onus if (o.pon, o.onu) == (1, 3))
        onu.serial = "VSOL00FFEE01"
        poll(env, olt["id"], done)
        status = env.client.get(
            f"/v1/monitoring/onus/{olt['id']}/1/3", headers=env.headers()
        ).json()
        assert status["status"]["customer_name"] is None

        # Vacío quita el nombre.
        env.client.put(url, json={"customer_name": "Nuevo"}, headers=env.headers())
        cleared = env.client.put(url, json={"customer_name": "   "}, headers=env.headers())
        assert cleared.json()["customer_name"] is None
