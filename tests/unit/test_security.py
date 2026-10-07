from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from olterra.executor.plan import Credential
from olterra.security import apikeys, sealed
from olterra.security.masking import MASK, redact
from olterra.security.vault import Vault, VaultError

# --- Bóveda -------------------------------------------------------------------------


def test_vault_roundtrip_and_tenant_binding(master_key: bytes) -> None:
    vault = Vault.single(master_key)
    tenant_a, tenant_b = uuid4(), uuid4()
    dek, wrapped = vault.new_tenant_key(tenant_a)
    assert vault.unwrap_tenant_key(tenant_a, wrapped) == dek
    # La DEK envuelta de A no abre como si fuera de B.
    with pytest.raises(VaultError):
        vault.unwrap_tenant_key(tenant_b, wrapped)

    blob = Vault.encrypt(tenant_a, dek, "credential:1", b"clave-olt")
    assert Vault.decrypt(tenant_a, dek, "credential:1", blob) == b"clave-olt"
    # Mover el ciphertext a otra fila (otro propósito) o a otro tenant no sirve.
    with pytest.raises(VaultError):
        Vault.decrypt(tenant_a, dek, "credential:2", blob)
    with pytest.raises(VaultError):
        Vault.decrypt(tenant_b, dek, "credential:1", blob)


def test_vault_detects_tampering(master_key: bytes) -> None:
    tenant = uuid4()
    dek, _ = Vault.single(master_key).new_tenant_key(tenant)
    blob = bytearray(Vault.encrypt(tenant, dek, "p", b"secreto"))
    blob[-1] ^= 0x01
    with pytest.raises(VaultError):
        Vault.decrypt(tenant, dek, "p", bytes(blob))


def test_vault_master_key_rotation(master_key: bytes) -> None:
    tenant = uuid4()
    old = Vault.single(master_key, version=1)
    dek, wrapped_v1 = old.new_tenant_key(tenant)
    new_key = os.urandom(32)
    rotated = Vault({1: master_key, 2: new_key}, current_version=2)
    assert rotated.needs_rewrap(wrapped_v1)
    assert rotated.unwrap_tenant_key(tenant, wrapped_v1) == dek
    wrapped_v2 = rotated.wrap_tenant_key(tenant, dek)
    assert not rotated.needs_rewrap(wrapped_v2)
    # Sin la llave vieja cargada, lo envuelto con v1 ya no abre.
    with pytest.raises(VaultError):
        Vault.single(new_key, version=2).unwrap_tenant_key(tenant, wrapped_v1)


def test_vault_rejects_bad_master_keys() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        Vault.single(b"corta")
    with pytest.raises(ValueError, match="versión"):
        Vault({1: os.urandom(32)}, current_version=2)


# --- Credenciales selladas ------------------------------------------------------------


def test_sealed_credential_only_opens_for_its_plan() -> None:
    private, public = sealed.generate_keypair()
    assert sealed.public_from_private(private) == public
    box = sealed.seal(public, b'{"password":"x"}', b"plan-1")
    assert sealed.unseal(private, box, b"plan-1") == b'{"password":"x"}'
    with pytest.raises(sealed.SealError):
        sealed.unseal(private, box, b"plan-2")
    other_private, _ = sealed.generate_keypair()
    with pytest.raises(sealed.SealError):
        sealed.unseal(other_private, box, b"plan-1")


def test_credential_reveal_json_does_not_mask() -> None:
    credential = Credential(username="olterra", password=SecretStr("Clave#2026"))
    assert "**********" in credential.model_dump_json()  # por eso existe reveal_json
    reopened = Credential.model_validate_json(credential.reveal_json())
    assert reopened.password is not None
    assert reopened.password.get_secret_value() == "Clave#2026"


# --- Llaves de API -------------------------------------------------------------------------


def test_api_key_roundtrip() -> None:
    tenant, key_id = uuid4(), uuid4()
    token, secret_hash = apikeys.generate(tenant, key_id)
    parsed = apikeys.parse(token)
    assert (parsed.tenant_id, parsed.key_id) == (tenant, key_id)
    assert apikeys.verify(parsed.secret, secret_hash)
    assert not apikeys.verify(b"x" * 32, secret_hash)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "olt_abc",
        "Bearer olt_x",
        "olt_" + "0" * 32 + "_" + "1" * 32 + "_corto",
        "xyz_" + "0" * 109,
    ],
)
def test_api_key_rejects_garbage(token: str) -> None:
    with pytest.raises(apikeys.InvalidApiKey):
        apikeys.parse(token)


# --- Enmascarado ------------------------------------------------------------------------


def test_redact_known_secrets_and_keywords() -> None:
    text = (
        "user add noc login-password Sup3rClave!\n"
        "snmp-server community comunidad-privada ro\n"
        "Password:\n"
        "gpon-olt# show onu info\n"
        "pppoe user cliente1 password 'abc 123'\n"
        "eco de la clave conocida: OltPass2026\n"
    )
    out = redact(text, ["OltPass2026"])
    vsol_onu = redact(
        "onu 3 pri wifi_ssid 1 name CASA auth_mode wpa2psk shared_key Wifi#2025 rekey_interval 0\n"
        "onu 3 pri wan_adv index 1 route ipv4 pppoe user cliente pwd Ppp#2025 mode auto\n"
    )
    assert "Wifi#2025" not in vsol_onu and "Ppp#2025" not in vsol_onu
    assert f"shared_key {MASK} rekey_interval 0" in vsol_onu
    assert "Sup3rClave!" not in out
    assert "comunidad-privada" not in out
    assert "OltPass2026" not in out
    assert "abc 123" not in out
    assert f"community {MASK} ro" in out
    # Un prompt "Password:" no se traga la línea siguiente.
    assert "gpon-olt# show onu info" in out


def test_redact_ignores_short_known_values() -> None:
    # Un "secreto" de 1-3 caracteres taparía medio texto; no se reemplaza.
    assert redact("ONU 1 activa", ["1"]) == "ONU 1 activa"
