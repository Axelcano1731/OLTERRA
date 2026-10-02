"""Credenciales selladas para el ejecutor.

La nube no manda claves en claro por NATS: los planes quedan guardados en
JetStream (en disco) hasta que el ejecutor los toma. Cada credencial va sellada
con la llave pública X25519 del ejecutor (ECIES: X25519 + HKDF-SHA256 +
AES-256-GCM) y el ``context`` (los ids del plan) va como AAD, así que una
credencial sellada no sirve en otro plan aunque alguien la copie.

Solo el ejecutor, con su llave privada, la abre: en memoria y para ese plan.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict

_INFO = b"olterra-sealed-v1"


class SealError(Exception):
    """La credencial sellada no abre con esta llave o este contexto."""


class SealedSecret(BaseModel):
    """Lo que viaja en el plan. Todo en base64 URL-safe sin relleno."""

    model_config = ConfigDict(frozen=True)

    epk: str  # llave pública efímera del remitente
    nonce: str
    ct: str


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_keypair() -> tuple[str, str]:
    """Devuelve ``(privada, pública)`` en base64 URL-safe."""
    private = X25519PrivateKey.generate()
    return _b64e(private.private_bytes_raw()), _b64e(private.public_key().public_bytes_raw())


def public_from_private(private_b64: str) -> str:
    private = X25519PrivateKey.from_private_bytes(_b64d(private_b64))
    return _b64e(private.public_key().public_bytes_raw())


def _derive(shared: bytes, epk: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + recipient, info=_INFO).derive(
        shared
    )


def seal(recipient_public_b64: str, plaintext: bytes, context: bytes) -> SealedSecret:
    recipient_bytes = _b64d(recipient_public_b64)
    recipient = X25519PublicKey.from_public_bytes(recipient_bytes)
    ephemeral = X25519PrivateKey.generate()
    epk = ephemeral.public_key().public_bytes_raw()
    key = _derive(ephemeral.exchange(recipient), epk, recipient_bytes)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, context)
    return SealedSecret(epk=_b64e(epk), nonce=_b64e(nonce), ct=_b64e(ct))


def unseal(recipient_private_b64: str, box: SealedSecret, context: bytes) -> bytes:
    try:
        private = X25519PrivateKey.from_private_bytes(_b64d(recipient_private_b64))
        epk = _b64d(box.epk)
        shared = private.exchange(X25519PublicKey.from_public_bytes(epk))
        key = _derive(shared, epk, private.public_key().public_bytes_raw())
        return AESGCM(key).decrypt(_b64d(box.nonce), _b64d(box.ct), context)
    except (InvalidTag, ValueError) as exc:
        raise SealError("La credencial sellada no abre con esta llave o en este plan") from exc
