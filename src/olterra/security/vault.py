"""Bóveda de credenciales: cifrado por sobre con una llave por tenant.

- **KEK** (llave maestra): fuera de la base de datos (``OLTERRA_MASTER_KEY``; más
  adelante un KMS). Tiene versión para poder rotarla sin descifrar todo de golpe.
- **DEK** (llave del tenant): 32 bytes aleatorios, guardada en ``tenant_keys``
  envuelta por la KEK.
- **Secreto**: AES-256-GCM con la DEK. El AAD amarra el texto cifrado a su tenant
  y a su propósito (por ejemplo ``credential:<id>``): un ciphertext copiado a otra
  fila u otro tenant no descifra.

Formato de los blobs (bytes): ``0x01 | versión KEK (2 bytes, solo en DEK) | nonce (12) | ct+tag``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_FORMAT = 1
_NONCE_LEN = 12


class VaultError(Exception):
    """Un blob no se pudo abrir: llave equivocada, datos alterados o formato inválido."""


def _dek_aad(tenant_id: UUID) -> bytes:
    return b"olterra/dek/v1|" + tenant_id.bytes


def _secret_aad(tenant_id: UUID, purpose: str) -> bytes:
    if not purpose:
        raise ValueError("purpose no puede estar vacío")
    return b"olterra/secret/v1|" + tenant_id.bytes + b"|" + purpose.encode("utf-8")


class Vault:
    def __init__(self, master_keys: Mapping[int, bytes], current_version: int) -> None:
        if current_version not in master_keys:
            raise ValueError(f"No hay llave maestra para la versión {current_version}")
        for version, key in master_keys.items():
            if len(key) != 32:
                raise ValueError(f"La llave maestra v{version} debe tener 32 bytes")
            if not 0 < version < 2**16:
                raise ValueError("La versión de la llave maestra va de 1 a 65535")
        self._keys = dict(master_keys)
        self._current = current_version

    @classmethod
    def single(cls, master_key: bytes, version: int = 1) -> Vault:
        return cls({version: master_key}, version)

    # --- Llaves de tenant ----------------------------------------------------

    def new_tenant_key(self, tenant_id: UUID) -> tuple[bytes, bytes]:
        """Genera una DEK nueva. Devuelve ``(dek, blob_envuelto)``."""
        dek = AESGCM.generate_key(bit_length=256)
        return dek, self.wrap_tenant_key(tenant_id, dek)

    def wrap_tenant_key(self, tenant_id: UUID, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(self._keys[self._current]).encrypt(nonce, dek, _dek_aad(tenant_id))
        return bytes([_FORMAT]) + self._current.to_bytes(2, "big") + nonce + ct

    def unwrap_tenant_key(self, tenant_id: UUID, blob: bytes) -> bytes:
        if len(blob) < 3 + _NONCE_LEN + 16 or blob[0] != _FORMAT:
            raise VaultError("Formato de llave de tenant desconocido")
        version = int.from_bytes(blob[1:3], "big")
        key = self._keys.get(version)
        if key is None:
            raise VaultError(f"No está cargada la llave maestra v{version}")
        nonce, ct = blob[3 : 3 + _NONCE_LEN], blob[3 + _NONCE_LEN :]
        try:
            return AESGCM(key).decrypt(nonce, ct, _dek_aad(tenant_id))
        except InvalidTag as exc:
            raise VaultError(
                "La llave del tenant no abre: KEK equivocada o datos alterados"
            ) from exc

    def needs_rewrap(self, blob: bytes) -> bool:
        """``True`` si la DEK está envuelta con una KEK que no es la vigente."""
        return int.from_bytes(blob[1:3], "big") != self._current

    # --- Secretos ------------------------------------------------------------

    @staticmethod
    def encrypt(tenant_id: UUID, dek: bytes, purpose: str, plaintext: bytes) -> bytes:
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(dek).encrypt(nonce, plaintext, _secret_aad(tenant_id, purpose))
        return bytes([_FORMAT]) + nonce + ct

    @staticmethod
    def decrypt(tenant_id: UUID, dek: bytes, purpose: str, blob: bytes) -> bytes:
        if len(blob) < 1 + _NONCE_LEN + 16 or blob[0] != _FORMAT:
            raise VaultError("Formato de secreto desconocido")
        nonce, ct = blob[1 : 1 + _NONCE_LEN], blob[1 + _NONCE_LEN :]
        try:
            return AESGCM(dek).decrypt(nonce, ct, _secret_aad(tenant_id, purpose))
        except InvalidTag as exc:
            raise VaultError(
                "El secreto no abre: otra llave, otro tenant, otro propósito o datos alterados"
            ) from exc
