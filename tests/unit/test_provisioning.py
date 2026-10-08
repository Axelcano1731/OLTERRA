"""Aprovisionamiento VSOL: copiar una ONU real y volver a generar su alta, comando por comando."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from olterra.drivers.base import ParamError
from olterra.drivers.vsol_gpon import ERRORS, VSOL_GPON
from olterra.drivers.vsol_gpon.provisioning import (
    ClientData,
    OnuServiceData,
    TemplateBody,
    authorize_calls,
    configure_calls,
    parse_onu_running_config,
)
from olterra.executor.plan import CliCommand, Credential, PlanResult, StepResult, Target
from olterra.orchestrator import PlanBuildError, build_write_plan, interpret
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
        "equipment_id": "VSOLV422",
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
        "equipment_id": "VSOLV422",
    }
    assert not any("pri equid" in line for line in copied.ignored)
    # Firewall y acceso desde la WAN también se copian (gestión remota).
    assert template.management is not None
    assert template.management.firewall == "low" and template.management.ping_wan
    assert template.management.wan_access == ["telnet", "http", "https"]
    assert copied.ignored == []
    assert not any("shared_key" in line and "******" not in line for line in copied.ignored)


def test_regenerated_commands_match_what_the_olt_saved() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    commands = rendered(template, client())
    saved = [line.strip() for line in running(3).splitlines() if line.startswith("onu ")]
    # La OLT agrega 'portid' al GEM y guarda 'pwd ******': todo lo demás, idéntico.
    # La OLT agrega 'port N' a algunas ACL (el puerto por defecto).
    saved = [
        re.sub(r" port \d+$", "", line)
        .replace(" portid 147", "")
        .replace("pwd ******", "pwd {{secret:pppoe_password}}")
        .replace("shared_key ******", "shared_key {{secret:wifi_key}}")
        for line in saved
    ]
    for command in commands[:-1]:  # el último es 'write'
        if " wan disable " in command:
            continue  # lo cerrado desde la WAN se dice explícito; la OLT no lo guarda
        assert command in saved, command
    assert "onu 3 pri acl ftp control enable lan enable wan disable" in " ".join(commands)
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
    with pytest.raises(ParamError, match="Equipment ID"):
        authorize_calls(template, client(equipment_id=None))
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


def test_equipment_id_goes_before_the_private_commands() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    keys = [c.key for c in authorize_calls(template, client())]
    # Sin "pri equid" la V1600G0-B responde "Unsupport private protocol" a la WAN y al WiFi.
    assert keys.index("onu.service_port") < keys.index("onu.pri_equid")
    assert keys.index("onu.pri_equid") < keys.index("onu.wan_add_route")
    assert keys.count("onu.pri_equid") == 1
    # Una plantilla sin WAN ni WiFi no usa el protocolo privado: no pide el Equipment ID.
    plain = TemplateBody(tconts=[], gemports=[])
    bare = client(equipment_id=None, wifi_ssid=None, wifi_key=None)
    assert "onu.pri_equid" not in [c.key for c in authorize_calls(plain, bare)]


def test_configure_an_onu_that_is_already_authorized() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    data = OnuServiceData(
        pon=1,
        onu=3,
        equipment_id="VSOLV422",
        pppoe_user="usuario-pppoe",
        pppoe_password=SecretStr(PPPOE_KEY),
    )
    keys = [c.key for c in configure_calls(template, data)]
    assert keys[0] == "onu.pri_equid" and keys[-1] == "config.save"
    assert "onu.authorize" not in keys and "onu.service_port" not in keys
    assert "onu.wifi_ssid" not in keys  # sin SSID no se toca el WiFi
    bridge = TemplateBody(wan=None, wifi=None)
    with pytest.raises(ParamError, match="ni gestión remota"):
        configure_calls(bridge, OnuServiceData(pon=1, onu=3))


def test_remote_management_opens_only_what_the_plan_says() -> None:
    template = TemplateBody.model_validate(
        {"management": {"firewall": "low", "ping_wan": False, "wan_access": ["https", "http"]}}
    )
    data = OnuServiceData(pon=1, onu=5, equipment_id="VSOLV422")
    commands = [
        VSOL_GPON.command(call.key, MODEL, FIRMWARE).render(**call.params)
        for call in configure_calls(template, data)
    ]
    tail = "ipv4_control disable ipv6_control disable"
    assert commands == [
        "onu 5 pri equid VSOLV422",
        "onu 5 pri firewall level low",
        f"onu 5 pri acl ping control enable lan enable wan disable {tail}",
        f"onu 5 pri acl telnet control enable lan enable wan disable {tail}",
        f"onu 5 pri acl ftp control enable lan enable wan disable {tail}",
        f"onu 5 pri acl http control enable lan enable wan enable {tail}",
        f"onu 5 pri acl https control enable lan enable wan enable {tail}",
        f"onu 5 pri acl tftp control enable lan enable wan disable {tail}",
        f"onu 5 pri acl ssh control enable lan enable wan disable {tail}",
        "write",
    ]
    # Sin Equipment ID la OLT no habla el protocolo privado: se avisa antes de tocarla.
    with pytest.raises(ParamError, match="gestión remota"):
        configure_calls(template, OnuServiceData(pon=1, onu=5))
    with pytest.raises(ValidationError):
        TemplateBody.model_validate({"management": {"wan_access": ["smtp"]}})
    with pytest.raises(ValidationError):
        TemplateBody.model_validate({"management": {"firewall": "off"}})


def test_onu_accounts_go_first_and_their_passwords_never_in_the_plan() -> None:
    management = {"firewall": "low", "wan_access": ["https"], "admin_user": "soporte"}
    data = OnuServiceData(pon=1, onu=5, equipment_id="VSOLV422")
    admin_only = configure_calls(TemplateBody.model_validate({"management": management}), data)
    command = VSOL_GPON.command(admin_only[1].key, MODEL, FIRMWARE).render(**admin_only[1].params)
    # Sintaxis de la ayuda de la V1600G0-B; la clave va como marcador, la pone el ejecutor.
    assert command == (
        "onu 5 pri username admin_control enable soporte {{secret:onu_admin_password}} "
        "user_control disable"
    )
    # Antes del firewall y de abrir la web.
    assert [c.key for c in admin_only][:3] == ["onu.pri_equid", "onu.account_admin", "onu.firewall"]
    both = TemplateBody.model_validate({"management": {**management, "user_account": "cliente"}})
    call = next(c for c in configure_calls(both, data) if c.key.startswith("onu.account"))
    assert (
        VSOL_GPON.command(call.key, MODEL, FIRMWARE)
        .render(**call.params)
        .endswith("user_control enable cliente {{secret:onu_user_password}}")
    )
    assert both.management is not None
    assert both.management.secret_fields() == ["onu_admin_password", "onu_user_password"]
    with pytest.raises(ValidationError, match="administración"):
        TemplateBody.model_validate({"management": {"user_account": "cliente"}})
    with pytest.raises(ValidationError):
        TemplateBody.model_validate({"management": {"admin_user": "con espacio"}})


def test_copying_an_onu_keeps_account_names_but_not_passwords() -> None:
    from olterra.security.masking import redact

    line = (
        "onu 3 pri username admin_control enable soporte Clave-Admin-1 "
        "user_control enable cliente Clave-User-2"
    )
    copied = parse_onu_running_config(running(3) + line + "\n")
    template = TemplateBody.model_validate(copied.template)
    assert template.management is not None
    assert template.management.admin_user == "soporte"
    assert template.management.user_account == "cliente"
    assert "Clave-Admin-1" not in str(copied.template) and copied.ignored == []
    # Si la OLT las muestra en claro, nunca llegan a la base ni a la pantalla.
    masked = redact(line)
    assert "Clave-Admin-1" not in masked and "Clave-User-2" not in masked
    assert masked.startswith("onu 3 pri username admin_control enable soporte ******")


def test_an_existing_wan_is_rewritten_not_added_again() -> None:
    template = TemplateBody.model_validate(parse_onu_running_config(running(3)).template)
    data = OnuServiceData(
        pon=1,
        onu=3,
        equipment_id="VSOLV422",
        pppoe_user="usuario-pppoe",
        pppoe_password=SecretStr(PPPOE_KEY),
    )
    from olterra.drivers.vsol_gpon.provisioning import service_calls

    first = [c.key for c in service_calls(template, data)]
    again = [c.key for c in service_calls(template, data, add_wan=False)]
    assert "onu.wan_add_route" in first and "onu.wan_add_route" not in again
    assert "onu.wan_pppoe" in again


def test_unsupported_private_protocol_is_an_error() -> None:
    patterns = [re.compile(p, re.MULTILINE) for p in ERRORS]
    assert any(p.search("Unsupport private protocol") for p in patterns)
    assert not any(p.search("set onu 3 reboot OK.") for p in patterns)


def test_failed_mode_change_steps_are_reported() -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    calls = [{"key": "config.save", "params": {}, "command": "write"}]
    commands = ["end", "write", "configure terminal", "interface gpon 0/1", "show onu state"]
    steps = [
        StepResult(index=0, ok=True, output=""),
        StepResult(index=1, ok=True, output="Configuration saved"),
        StepResult(index=2, ok=False, error="La OLT no devolvió el prompt a tiempo"),
        StepResult(index=3, ok=False, error="No se ejecutó: falló el paso 2"),
        StepResult(index=4, ok=False, error="No se ejecutó: falló el paso 2"),
    ]
    result = PlanResult(
        plan_id=uuid4(),
        tenant_id=uuid4(),
        olt_id=uuid4(),
        executor="x",
        status="partial",
        steps=steps,
        started_at=now,
        finished_at=now,
    )
    failed = {
        "command": "configure terminal",
        "error": "La OLT no devolvió el prompt a tiempo",
        "output": None,
    }
    assert interpret(calls, commands, result)["session_errors"] == [failed]


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


def test_names_are_cleaned_for_the_olt() -> None:
    from olterra.drivers.vsol_gpon.provisioning import clean_label

    assert clean_label("José Pérez  Ñuñez", limit=64) == "Jose_Perez_Nunez"
    assert clean_label("  Casa de Ana #2 ", limit=32) == "Casa_de_Ana_2"
    assert clean_label("x" * 80, limit=64) == "x" * 64
    with pytest.raises(ParamError):
        clean_label("¿?¡!", limit=10)


def test_wan_ports_follow_the_onu_model() -> None:
    from olterra.drivers.vsol_gpon.provisioning import adapt_binds

    binds = ["lan1", "lan2", "lan3", "lan4", "ssid1", "ssid2"]
    assert adapt_binds(binds, 2, 1) == ["lan1", "lan2", "ssid1", "ssid2"]  # V422: 2 LAN
    assert adapt_binds(binds, 4, 1) == binds  # V824: 4 LAN
    assert adapt_binds(binds, 4, 0) == ["lan1", "lan2", "lan3", "lan4"]  # sin WiFi
    assert adapt_binds(["ssid1"], 1, 1) == ["lan1", "ssid1"]  # siempre al menos una LAN
    assert adapt_binds(binds, None, None) == binds  # sin dato, como dice la plantilla
