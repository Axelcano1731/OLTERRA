"""Contrato nube ↔ ejecutor: planes que bajan y resultados que suben.

Es el único acoplamiento entre los dos lados, así que lleva versión (``version``)
y todo campo nuevo debe ser opcional para que un ejecutor viejo no rechace planes
nuevos que no usa.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from olterra.security.sealed import SealedSecret


class Priority(IntEnum):
    """Menor número = se atiende antes, dentro de la cola de cada OLT."""

    USER = 0  # alguien está esperando en pantalla
    PROVISION = 1  # aprovisionamiento automático (alta, corte, reconexión)
    POLL = 2  # monitoreo periódico


class SessionProfile(BaseModel):
    """Cómo hablarle a la CLI de un modelo. Lo arma el driver; el ejecutor lo obedece."""

    model_config = ConfigDict(frozen=True)

    # Se compara con la ÚLTIMA línea recibida (fullmatch), nunca con el texto intermedio.
    prompt_pattern: str
    # Si la última línea calza con esto, la CLI está paginando: se manda pager_response.
    pager_pattern: str | None = None
    pager_response: str = " "
    enable_command: str | None = None
    enable_password_pattern: str | None = None
    # Comandos de preparación tras entrar (paginación off). Si fallan, se sigue.
    setup_commands: tuple[str, ...] = ()
    # Mensajes de error de la CLI: si una salida calza, el paso cuenta como fallido.
    error_patterns: tuple[str, ...] = ()
    # Modelos cuyo SSH cae en una shell y hay que entrar a la CLI (p. ej. vtysh).
    shell_entry_commands: tuple[str, ...] = ()
    newline: str = "\n"
    login_timeout_s: float = 20.0
    # Algunas OLT solo hablan algoritmos SSH viejos (group1-sha1, ssh-rsa, cbc).
    legacy_ssh_algorithms: bool = False
    terminal_width: int = 512


# Un paso CLI puede llevar ``{{secret:<campo>}}``: el ejecutor lo reemplaza con el campo de la
# credencial sellada justo antes de escribirlo en la OLT. Así una clave nueva nunca viaja en claro
# por NATS ni queda en el plan guardado.
SECRET_FIELDS = ("password", "enable_password", "new_password", "new_enable_password")
_SECRET_PLACEHOLDER = re.compile(r"\{\{secret:([a-z_]+)\}\}")


def secret_marker(field: str) -> str:
    if field not in SECRET_FIELDS:
        raise ValueError(f"Campo secreto desconocido: {field}")
    return "{{secret:" + field + "}}"


class CliCommand(BaseModel):
    kind: Literal["cli"] = "cli"
    command: str
    timeout_s: float = 30.0
    # Si es True, el comando no se devuelve en claro en el resultado (lleva una clave).
    sensitive: bool = False

    @field_validator("command")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if any(ch in value for ch in "\r\n") or any(ord(ch) < 32 and ch != "\t" for ch in value):
            raise ValueError("Un comando CLI va en una sola línea y sin caracteres de control")
        for name in _SECRET_PLACEHOLDER.findall(value):
            if name not in SECRET_FIELDS:
                raise ValueError(f"Campo secreto desconocido: {name}")
        return value

    def uses_secrets(self) -> bool:
        return _SECRET_PLACEHOLDER.search(self.command) is not None


class SnmpWalk(BaseModel):
    kind: Literal["snmp_walk"] = "snmp_walk"
    oid: str
    max_repetitions: int = 25
    timeout_s: float = 10.0
    retries: int = 1

    @field_validator("oid")
    @classmethod
    def _numeric_oid(cls, value: str) -> str:
        parts = value.strip(".").split(".")
        if not parts or not all(p.isdigit() for p in parts):
            raise ValueError("El OID va en forma numérica, por ejemplo 1.3.6.1.2.1.1")
        return ".".join(parts)


Step = Annotated[CliCommand | SnmpWalk, Field(discriminator="kind")]


class Target(BaseModel):
    host: str
    ssh_port: int = 22
    snmp_port: int = 161
    # Llave de host conocida (formato OpenSSH: "ssh-rsa AAAA..."). Si falta, se
    # acepta la que presente la OLT y se reporta su huella (confianza al primer uso).
    ssh_host_key: str | None = None


class Credential(BaseModel):
    """Lo que viaja sellado dentro de ``Plan.credential``."""

    username: str | None = None
    password: SecretStr | None = None
    enable_password: SecretStr | None = None
    snmp_community: SecretStr | None = None
    snmp_v3_user: str | None = None
    snmp_v3_auth_key: SecretStr | None = None
    snmp_v3_priv_key: SecretStr | None = None
    # Solo en planes que cambian las claves de acceso: lo que se va a poner en la OLT.
    new_password: SecretStr | None = None
    new_enable_password: SecretStr | None = None

    def resolve_secrets(self, command: str) -> str:
        """Reemplaza los ``{{secret:campo}}`` de un comando. Falla si falta algún valor."""

        def replace(match: re.Match[str]) -> str:
            value = getattr(self, match.group(1), None)
            if not isinstance(value, SecretStr) or not value.get_secret_value():
                raise ValueError(f"La credencial sellada no trae '{match.group(1)}'")
            return value.get_secret_value()

        resolved = _SECRET_PLACEHOLDER.sub(replace, command)
        # Una clave con salto de línea sería otro comando en la OLT.
        if any(not ch.isprintable() for ch in resolved):
            raise ValueError("El comando resuelto lleva caracteres de control")
        return resolved

    def reveal_json(self) -> bytes:
        """JSON con los secretos EN CLARO, solo para sellarlo o cifrarlo enseguida.

        ``model_dump_json`` de pydantic tapa los ``SecretStr`` con asteriscos: usarlo
        para sellar mandaría "**********" como clave.
        """
        data = {
            name: value.get_secret_value() if isinstance(value, SecretStr) else value
            for name, value in self
        }
        return json.dumps(data).encode()

    def secret_values(self) -> list[str]:
        """Valores a tapar en cualquier salida que salga del ejecutor."""
        values = [
            self.password,
            self.enable_password,
            self.snmp_community,
            self.snmp_v3_auth_key,
            self.snmp_v3_priv_key,
            self.new_password,
            self.new_enable_password,
        ]
        return [v.get_secret_value() for v in values if v is not None]


class Plan(BaseModel):
    version: Literal[1] = 1
    plan_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    olt_id: UUID
    priority: Priority = Priority.POLL
    access: Literal["read", "write"] = "read"
    # "stop": ante el primer paso fallido no se corre el resto (escrituras).
    # "continue": se corren todos (lecturas, capturas de laboratorio).
    on_error: Literal["stop", "continue"] = "stop"
    target: Target
    session: SessionProfile | None = None
    credential: SealedSecret | None = None
    steps: list[Step]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    deadline: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=5))

    def seal_context(self) -> bytes:
        """AAD de la credencial sellada: la amarra a este plan, tenant y OLT."""
        return b"olterra-plan|" + self.plan_id.bytes + self.tenant_id.bytes + self.olt_id.bytes

    def needs_cli(self) -> bool:
        return any(isinstance(step, CliCommand) for step in self.steps)


class Varbind(BaseModel):
    oid: str
    type: str
    value: str


class StepResult(BaseModel):
    index: int
    ok: bool
    output: str | None = None
    varbinds: list[Varbind] | None = None
    error: str | None = None
    elapsed_ms: int = 0


PlanStatus = Literal["ok", "partial", "failed", "expired", "rejected"]


class PlanResult(BaseModel):
    version: Literal[1] = 1
    plan_id: UUID
    tenant_id: UUID
    olt_id: UUID
    executor: str
    status: PlanStatus
    steps: list[StepResult] = Field(default_factory=list)
    error: str | None = None
    host_key: str | None = None
    started_at: datetime
    finished_at: datetime
