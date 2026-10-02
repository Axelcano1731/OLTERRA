"""SNMP walk por tabla (GETBULK) con pysnmp."""

from __future__ import annotations

from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_walk_cmd,
    usmAesCfb128Protocol,
    usmHMACSHAAuthProtocol,
)

from olterra.executor.plan import Credential, Varbind


class SnmpError(Exception):
    pass


def render_value(value: Any) -> tuple[str, str]:
    """``(tipo, texto)`` de un valor SNMP. Los OCTET STRING binarios van en hex ``0x…``."""
    type_name = type(value).__name__
    if type_name == "OctetString":
        raw: bytes = value.asOctets()
        if all(32 <= b < 127 or b in (9, 10, 13) for b in raw):
            return type_name, raw.decode("ascii")
        return type_name, "0x" + raw.hex()
    return type_name, value.prettyPrint()


def _auth_data(credential: Credential) -> CommunityData | UsmUserData:
    if credential.snmp_v3_user:
        auth = (
            credential.snmp_v3_auth_key.get_secret_value() if credential.snmp_v3_auth_key else None
        )
        priv = (
            credential.snmp_v3_priv_key.get_secret_value() if credential.snmp_v3_priv_key else None
        )
        return UsmUserData(
            credential.snmp_v3_user,
            authKey=auth,
            privKey=priv,
            authProtocol=usmHMACSHAAuthProtocol if auth else None,
            privProtocol=usmAesCfb128Protocol if priv else None,
        )
    if credential.snmp_community is None:
        raise SnmpError("El plan pide SNMP y la credencial no trae comunidad ni usuario v3")
    # v2c sigue siendo lo habitual en OLT; la comunidad se restringe a la IP del túnel.
    return CommunityData(credential.snmp_community.get_secret_value(), mpModel=1)  # noqa: S508


class SnmpClient:
    """Un motor SNMP por proceso; cada walk es una corrida independiente."""

    def __init__(self) -> None:
        self._engine = SnmpEngine()

    async def walk(
        self,
        host: str,
        port: int,
        credential: Credential,
        oid: str,
        *,
        max_repetitions: int = 25,
        timeout: float = 10.0,
        retries: int = 1,
        max_rows: int = 200_000,
    ) -> list[Varbind]:
        target = await UdpTransportTarget.create((host, port), timeout=timeout, retries=retries)
        result: list[Varbind] = []
        async for error_indication, error_status, error_index, var_binds in bulk_walk_cmd(
            self._engine,
            _auth_data(credential),
            target,
            ContextData(),
            0,
            max_repetitions,
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
            lookupMib=False,
        ):
            if error_indication:
                raise SnmpError(f"SNMP sin respuesta de {host}: {error_indication}")
            if error_status:
                raise SnmpError(
                    f"SNMP devolvió {error_status.prettyPrint()} en el índice {error_index}"
                )
            for var_bind in var_binds:
                name, value = var_bind[0], var_bind[1]
                type_name, text = render_value(value)
                if type_name in ("NoSuchObject", "NoSuchInstance", "EndOfMibView"):
                    continue
                result.append(Varbind(oid=str(name), type=type_name, value=text))
                if len(result) > max_rows:
                    raise SnmpError("La tabla SNMP excede el máximo de filas permitido")
        return result

    def close(self) -> None:
        self._engine.close_dispatcher()
