"""Llaves de API por tenant.

Formato: ``olt_<tenant>_<id>_<secreto>`` (tenant e id en hex de 32, secreto de 32
bytes en base64 URL-safe). Llevar el tenant dentro de la llave permite buscarla
YA dentro de RLS: la API fija ``olterra.tenant_id`` con el tenant que dice la
llave y la busca por id; si la llave es de otro tenant, la base no la devuelve.
Así no hace falta ninguna función que se salte el aislamiento para autenticar.

En la base solo se guarda el SHA-256 del secreto (tiene 256 bits de entropía, no
necesita un hash lento como una contraseña).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

PREFIX = "olt"
_PATTERN = re.compile(r"^olt_([0-9a-f]{32})_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")


class InvalidApiKey(ValueError):
    """La llave no tiene el formato de Olterra."""


@dataclass(frozen=True)
class ParsedKey:
    tenant_id: UUID
    key_id: UUID
    secret: bytes


def hash_secret(secret: bytes) -> bytes:
    return hashlib.sha256(secret).digest()


def generate(tenant_id: UUID, key_id: UUID) -> tuple[str, bytes]:
    """Devuelve ``(llave_completa, hash_del_secreto)``. La llave se muestra una sola vez."""
    secret = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    return f"{PREFIX}_{tenant_id.hex}_{key_id.hex}_{encoded}", hash_secret(secret)


def parse(token: str) -> ParsedKey:
    match = _PATTERN.fullmatch(token.strip())
    if match is None:
        raise InvalidApiKey("Llave de API con formato inválido")
    tenant_hex, key_hex, encoded = match.groups()
    secret = base64.urlsafe_b64decode(encoded + "=")
    return ParsedKey(UUID(hex=tenant_hex), UUID(hex=key_hex), secret)


def verify(secret: bytes, expected_hash: bytes) -> bool:
    return hmac.compare_digest(hash_secret(secret), expected_hash)
