"""Tokens de sesión de los usuarios que entran con usuario y contraseña.

Formato: ``ols_<tenant>_<id>_<secreto>``, el mismo diseño que las llaves de API
(``security/apikeys.py``): el tenant va dentro del token, así la sesión se busca YA dentro de
RLS y no hace falta ninguna función que se salte el aislamiento para validarla. En la base solo
queda el SHA-256 del secreto.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

PREFIX = "ols"
_PATTERN = re.compile(r"^ols_([0-9a-f]{32})_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")


class InvalidSessionToken(ValueError):
    pass


@dataclass(frozen=True)
class ParsedSession:
    tenant_id: UUID
    session_id: UUID
    secret: bytes


def looks_like_session(token: str) -> bool:
    return token.strip().startswith(f"{PREFIX}_")


def hash_secret(secret: bytes) -> bytes:
    return hashlib.sha256(secret).digest()


def generate(tenant_id: UUID, session_id: UUID) -> tuple[str, bytes]:
    """``(token, hash_del_secreto)``. El token se entrega una vez, al entrar."""
    secret = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    return f"{PREFIX}_{tenant_id.hex}_{session_id.hex}_{encoded}", hash_secret(secret)


def parse(token: str) -> ParsedSession:
    match = _PATTERN.fullmatch(token.strip())
    if match is None:
        raise InvalidSessionToken("Token de sesión con formato inválido")
    tenant_hex, session_hex, encoded = match.groups()
    return ParsedSession(
        UUID(hex=tenant_hex), UUID(hex=session_hex), base64.urlsafe_b64decode(encoded + "=")
    )


def verify(secret: bytes, expected_hash: bytes) -> bool:
    return hmac.compare_digest(hash_secret(secret), expected_hash)
