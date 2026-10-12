"""Cambiar las claves de una OLT: la clave nueva viaja solo sellada, nunca en el plan."""

from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from olterra.drivers.vsol_gpon import VSOL_GPON
from olterra.executor.plan import CliCommand, Credential, Plan, Target
from olterra.executor.runner import PlanRunner
from olterra.executor.ssh import SshConnectOptions
from olterra.orchestrator import PlanBuildError, build_credential_change_plan
from olterra.security.sealed import generate_keypair, unseal
from tests.unit.test_cli_session import ScriptedTransport

NEW_LOGIN = "Nueva#Clave-2026"
NEW_ENABLE = "Enable#Nueva-2026"


def verified_driver():  # type: ignore[no-untyped-def]
    commands = {
        key: dataclasses.replace(template, verified=True)
        if key in {"user.set_login_password", "user.set_enable_password", "config.save"}
        else template
        for key, template in VSOL_GPON.commands.items()
    }
    return dataclasses.replace(VSOL_GPON, commands=commands)


def credential(**extra: SecretStr | None) -> Credential:
    return Credential(
        username="olterra",
        password=SecretStr("Vieja#Clave-2025"),
        enable_password=SecretStr("Enable#Vieja"),
        **extra,
    )


def build(driver=None, **extra: SecretStr | None) -> tuple[Plan, str]:  # type: ignore[no-untyped-def]
    private, public = generate_keypair()
    plan = build_credential_change_plan(
        driver or verified_driver(),
        tenant_id=uuid4(),
        olt_id=uuid4(),
        target=Target(host="10.0.0.1"),
        credential=credential(**extra),
        executor_public_key=public,
    )
    return plan, private


def test_new_passwords_never_appear_in_the_plan() -> None:
    plan, private = build(
        new_password=SecretStr(NEW_LOGIN), new_enable_password=SecretStr(NEW_ENABLE)
    )
    dumped = plan.model_dump_json()
    for secret in (NEW_LOGIN, NEW_ENABLE, "Vieja#Clave-2025", "Enable#Vieja"):
        assert secret not in dumped
    assert plan.access == "write" and plan.on_error == "stop"
    steps = {step.command: step for step in plan.steps if isinstance(step, CliCommand)}
    # La clave no va en la línea: la OLT la pregunta después del Enter y el ejecutor contesta.
    login, enable = steps["user login-password olterra"], steps["user enable-password olterra"]
    assert [r.answer for r in login.prompts] == ["{{secret:new_password}}"]
    assert [r.answer for r in enable.prompts] == ["{{secret:new_enable_password}}"]
    assert login.prompts[0].max_times == 2  # la clave y su confirmación
    # Sin "write": se guarda después, cuando otra sesión entró con la clave nueva.
    assert list(steps)[-1] == "end"
    assert "write" not in steps

    # Lo que sí lleva el sello: las claves, que solo el ejecutor puede abrir.
    assert plan.credential is not None
    opened = Credential.model_validate_json(unseal(private, plan.credential, plan.seal_context()))
    assert opened.new_password is not None
    assert opened.new_password.get_secret_value() == NEW_LOGIN


def test_plan_needs_a_new_password_and_a_verified_syntax() -> None:
    with pytest.raises(PlanBuildError, match="ninguna clave nueva"):
        build()
    with pytest.raises(PlanBuildError, match="no se validó en laboratorio"):
        build(VSOL_GPON, new_password=SecretStr(NEW_LOGIN))  # hoy: sin captura de laboratorio
    _, public = generate_keypair()
    lab = build_credential_change_plan(  # modo laboratorio
        VSOL_GPON,
        tenant_id=uuid4(),
        olt_id=uuid4(),
        target=Target(host="10.0.0.1"),
        credential=credential(new_password=SecretStr(NEW_LOGIN)),
        executor_public_key=public,
        allow_unverified=True,
    )
    assert lab.access == "write"


def test_unknown_secret_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Campo secreto desconocido"):
        CliCommand(command="user x {{secret:snmp_community}}")
    with pytest.raises(ValidationError):
        CliCommand(command="user x {{secret:__class__}}")


def test_resolve_refuses_missing_values_and_control_characters() -> None:
    with pytest.raises(ValueError, match="no trae 'new_password'"):
        credential().resolve_secrets("x {{secret:new_password}}")
    bad = credential(new_password=SecretStr("clave\nreload"))
    with pytest.raises(ValueError, match="caracteres de control"):
        bad.resolve_secrets("x {{secret:new_password}}")
    # Una sola pasada: una clave que parece un marcador no se vuelve a expandir.
    tricky = credential(new_password=SecretStr("{{secret:password}}"))
    assert tricky.resolve_secrets("x {{secret:new_password}}") == "x {{secret:password}}"


def device(written: list[str]) -> ScriptedTransport:
    """OLT de mentira que pregunta la clave nueva y la confirmación después del Enter."""
    state = {"prompt": "gpon-olt>", "awaiting": "", "first": ""}

    def reply(data: str) -> list[str]:
        line = data.strip()
        written.append(line)
        if state["awaiting"] == "enable":
            state["awaiting"], state["prompt"] = "", "gpon-olt#"
            return ["\r\n", "gpon-olt#"]
        if state["awaiting"] == "new":
            state["awaiting"], state["first"] = "confirm", line
            return ["\r\n", "Confirm password:"]
        if state["awaiting"] == "confirm":
            state["awaiting"] = ""
            ok = line == state["first"]
            # Esta OLT de mentira repite la clave en su aviso: tampoco debe salir en el resultado.
            text = f"Password {line} changed\r\n" if ok else "% Error: passwords mismatch\r\n"
            return ["\r\n" + text, str(state["prompt"])]
        echo = line + "\r\n"
        if line == "enable":
            state["awaiting"] = "enable"
            return [echo, "Password:"]
        if line == "configure terminal":
            state["prompt"] = "gpon-olt(config)#"
        if line.startswith("user "):
            state["awaiting"] = "new"
            return [echo, "New password:"]
        if line == "end":
            state["prompt"] = "gpon-olt#"
        return [echo, str(state["prompt"])]

    return ScriptedTransport(reply, ["gpon-olt>"])


async def test_runner_puts_the_real_password_on_the_wire_but_not_in_the_result() -> None:
    plan, private = build(
        new_password=SecretStr(NEW_LOGIN), new_enable_password=SecretStr(NEW_ENABLE)
    )
    assert plan.credential is not None
    opened = Credential.model_validate_json(unseal(private, plan.credential, plan.seal_context()))
    written: list[str] = []

    async def factory(options: SshConnectOptions) -> ScriptedTransport:
        assert options.password == "Vieja#Clave-2025"  # entra con la vigente
        return device(written)

    result = await PlanRunner("prueba", transport_factory=factory).run(plan, opened)
    assert result.status == "ok", result.model_dump_json(indent=1)
    login = written.index("user login-password olterra")
    assert written[login + 1 : login + 3] == [NEW_LOGIN, NEW_LOGIN]
    enable = written.index("user enable-password olterra")
    assert written[enable + 1 : enable + 3] == [NEW_ENABLE, NEW_ENABLE]
    dumped = result.model_dump_json()
    assert NEW_LOGIN not in dumped and NEW_ENABLE not in dumped  # ni siquiera el eco de la OLT


async def test_a_missing_secret_stops_the_plan_without_sending_anything() -> None:
    plan, _ = build(new_password=SecretStr(NEW_LOGIN))
    without_new = credential()  # el ejecutor abrió un sello sin la clave nueva
    written: list[str] = []

    async def factory(options: SshConnectOptions) -> ScriptedTransport:
        return device(written)

    result = await PlanRunner("prueba", transport_factory=factory).run(plan, without_new)
    assert result.status != "ok"
    assert not any(line.startswith("user ") for line in written)
    assert "write" not in written  # tampoco guardó nada
