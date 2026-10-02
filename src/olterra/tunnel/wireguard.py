"""Llaves WireGuard (Curve25519, base64 estándar como las muestra RouterOS).

La plataforma genera el par del router del ISP. Así se puede entregar un script
completo de una vez, sin pedir antes la llave pública del router (el huevo y la
gallina que ISPWatch ya resolvió igual). La privada va en el script y NO se guarda:
si se necesita el script otra vez, se rota el par.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


class InvalidKey(ValueError):
    pass


def generate_keypair() -> tuple[str, str]:
    """``(privada, pública)`` en base64."""
    private = X25519PrivateKey.generate()
    return (
        base64.b64encode(private.private_bytes_raw()).decode("ascii"),
        base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii"),
    )


def public_from_private(private_b64: str) -> str:
    raw = _decode(private_b64)
    public = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes_raw()
    return base64.b64encode(public).decode("ascii")


def _decode(key_b64: str) -> bytes:
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidKey("La llave WireGuard no es base64 válido") from exc
    if len(raw) != 32:
        raise InvalidKey("Una llave WireGuard tiene 32 bytes")
    return raw


def validate_key(key_b64: str) -> str:
    _decode(key_b64)
    return key_b64
