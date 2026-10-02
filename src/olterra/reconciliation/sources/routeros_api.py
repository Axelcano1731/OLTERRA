"""Lectura en vivo del MikroTik BNG por la API de RouterOS (solo lectura).

Se piden columnas explícitas (``.proplist``): la clave de cada secreto ni
siquiera viaja por la red. Conviene un usuario de grupo ``read`` dedicado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from olterra.reconciliation.model import PppoeSecret, PppoeSession
from olterra.reconciliation.sources import SourceError


@dataclass(frozen=True)
class RouterOsTarget:
    name: str
    host: str
    username: str
    password: str
    port: int = 8728
    use_tls: bool = False


def _connect(target: RouterOsTarget) -> Any:
    import ssl

    from librouteros import connect

    kwargs: dict[str, Any] = {"port": target.port, "encoding": "latin-1", "timeout": 15}
    if target.use_tls:
        context = ssl.create_default_context()
        # Los routers usan certificados propios; el canal va cifrado igual.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        kwargs["ssl_wrapper"] = context.wrap_socket
    try:
        return connect(target.host, target.username, target.password, **kwargs)
    except Exception as exc:
        raise SourceError(f"No se pudo entrar a {target.name} ({target.host}): {exc}") from exc


def read_pppoe(target: RouterOsTarget) -> tuple[list[PppoeSecret], list[PppoeSession]]:
    from librouteros.query import Key

    api = _connect(target)
    try:
        secrets = [
            PppoeSecret(
                router=target.name,
                name=row["name"],
                profile=row.get("profile"),
                disabled=bool(row.get("disabled")),
                comment=row.get("comment"),
            )
            for row in api.path("ppp", "secret").select(
                Key("name"), Key("profile"), Key("disabled"), Key("comment"), Key("service")
            )
            if row.get("name") and row.get("service", "any") in ("pppoe", "any")
        ]
        sessions = [
            PppoeSession(
                router=target.name,
                name=row["name"],
                caller_id=row.get("caller-id"),
                address=row.get("address"),
            )
            for row in api.path("ppp", "active").select(
                Key("name"), Key("caller-id"), Key("address"), Key("service")
            )
            if row.get("name") and row.get("service", "pppoe") == "pppoe"
        ]
    finally:
        api.close()
    return secrets, sessions
