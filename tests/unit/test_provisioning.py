"""Aprovisionamiento VSOL: copiar una ONU real y volver a generar su alta, comando por comando."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from olterra.drivers.base import ParamError
from olterra.drivers.vsol_gpon import VSOL_GPON
from olterra.drivers.vsol_gpon.provisioning import (
    ClientData,
    TemplateBody,
    authorize_calls,
    parse_onu_running_config,
)
from olterra.executor.plan import CliCommand, Credential, Target
from olterra.orchestrator import PlanBuildError, build_write_plan
from olterra.security.sealed import generate_keypair

FIXTURE = Path("tests/fixtures/vsol-gpon/V1600G0-B/V1.4.8R/20261003/cli")
MODEL, FIRMWARE = "V1600G0-B", "V1.4.8R"
PPPOE_KEY, WIFI_KEY = "Ppp#Clave-2026", "Wifi#Clave-2026"


def running(onu: int) -> str:
    return (FIXTURE / f"onu.service_config_pon1_onu{onu}.txt").read_text(encoding="utf-8")


def client(**overrides: object) -> ClientData:
    data: dict[str, object] = {
        "pon": 1,
        "onu": 3,
        "serial": "GPONE5197ED0",
        "description": "CLIENTE-PRUEBA",
        "pppoe_user": "usuario-pppoe",
        "pppoe_password": SecretStr(PPPOE_KEY),
        "wifi_ssid": "WIFI-CLIENTE",
        "wifi_key": SecretStr(WIFI_KEY),
    } | overrides
    return ClientData.model_validate(data)


def rendered(template: TemplateBody, data: ClientData) -> list[str]:
    return [
        VSOL_GPON.command(call.key, MODEL, FIRMWARE).render(**call.params)
        for call in authorize_calls(template, data)
    ]


def test_copy_a_real_onu_into_a_template() -> None:
    copied = parse_onu_running_config(running(3))
    template = TemplateBody.model_validate(copied.template)
    # "onu N profile onu default" repite el perfil de "onu add": la V1600G0-B lo rechaza.
    assert template.auth_profile == "default" and template.onu_profile is None
    assert [(t.id, t.name, t.dba) for t in template.tconts] == [(1, "INTERNET", "default1")]
    assert template.gemports[0].limit_down == "default"
    assert [(s.vlan, s.gemport) for s in template.services] == [(111, 1)]
    assert template.wan is not None and template.wan.vlan == 111 and template.wan.nat
    assert template.wan.binds == ["lan1", "lan2", "ssid1"]
    assert template.wifi is not None
    # Lo del cliente se separa de la plantilla; las claves no se copian.
    assert copied.client == {
        "serial": "GPONE5197ED0",
        "description": "CLIENTE-PRUEBA",
        "pppoe_user": "usuario-pppoe",
        "wifi_ssid": "WIFI-CLIENTE",
    }
    assert any(line.startswith("onu 3 pri acl") for line in copied.ignored)
    assert not any("shared_key" in line and "******" not in line for line in copied.ignored)


def test_regenerated_commands_match_what_the_olt_saved() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    commands = rendered(template, client())
    saved = [line.strip() for line in running(3).splitlines() if line.startswith("onu ")]
    # La OLT agrega 'portid' al GEM y guarda 'pwd ******': todo lo demás, idéntico.
    saved = [
        line.replace(" portid 147", "")
        .replace("pwd ******", "pwd {{secret:pppoe_password}}")
        .replace("shared_key ******", "shared_key {{secret:wifi_key}}")
        for line in saved
    ]
    for command in commands[:-1]:  # el último es 'write'
        assert command in saved, command
    assert commands[-1] == "write"
    assert not any(PPPOE_KEY in c or WIFI_KEY in c for c in commands)


def test_the_bridge_onu_without_wifi_also_copies() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(2)).template)
    assert template.wifi is None and template.wan is not None
    commands = rendered(template, client(onu=7, wifi_ssid=None, wifi_key=None))
    assert "onu 7 service-port 1 gemport 1 uservlan 111 vlan 111 new_cos 0" in commands
    assert not any("wifi_ssid" in c for c in commands)


def test_client_data_is_checked_before_anything_reaches_the_olt() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    with pytest.raises(ParamError, match="PPPoE"):
        authorize_calls(template, client(pppoe_password=None))
    with pytest.raises(ParamError, match="SSID y clave"):
        authorize_calls(template, client(wifi_key=None))
    for bad in ({"description": "nombre con espacios"}, {"serial": "123"}, {"pppoe_user": "a b"}):
        with pytest.raises(ValidationError):
            client(**bad)
    # Una clave con '?' abriría la ayuda de la CLI; con salto de línea sería otro comando.
    for key in ("con?pregunta", "con\nsalto", "corta"):
        with pytest.raises(ValidationError):
            client(wifi_key=SecretStr(key))


def test_template_references_must_exist() -> None:
    with pytest.raises(ValidationError, match="T-CONT 2"):
        TemplateBody.model_validate(
            {"tconts": [{"id": 1, "dba": "d"}], "gemports": [{"id": 1, "tcont": 2}]}
        )
    with pytest.raises(ValidationError, match="GEM 5"):
        TemplateBody.model_validate({"services": [{"gemport": 5, "vlan": 10}]})


def test_write_plan_is_refused_until_the_lab_validates_it() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    calls = authorize_calls(template, client())
    _, public = generate_keypair()
    common = {
        "tenant_id": uuid4(),
        "olt_id": uuid4(),
        "target": Target(host="10.0.0.1"),
        "calls": calls,
        "credential": Credential(
            username="admin",
            password=SecretStr("x"),
            pppoe_password=SecretStr(PPPOE_KEY),
            wifi_key=SecretStr(WIFI_KEY),
        ),
        "executor_public_key": public,
        "model": MODEL,
        "firmware": FIRMWARE,
    }
    with pytest.raises(PlanBuildError, match=r"onu.authorize"):
        build_write_plan(VSOL_GPON, **common)  # type: ignore[arg-type]
    plan = build_write_plan(VSOL_GPON, allow_unverified=True, **common)  # type: ignore[arg-type]
    assert plan.access == "write" and plan.on_error == "stop"
    dumped = plan.model_dump_json()
    assert PPPOE_KEY not in dumped and WIFI_KEY not in dumped
    commands = [s.command for s in plan.steps if isinstance(s, CliCommand)]
    assert commands[:2] == ["configure terminal", "interface gpon 0/1"]
    assert "write" in commands
