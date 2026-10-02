"""Configuración central, leída de variables de entorno con prefijo ``OLTERRA_``.

Cada proceso (API, ejecutor, herramientas de línea de comandos) usa solo la parte
que le toca; los valores por defecto sirven para el entorno de desarrollo con
``docker compose`` y nunca para producción, donde ``OLTERRA_ENV=prod`` obliga a
definir los secretos.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    """La configuración no permite arrancar el proceso de forma segura."""


def decode_key(value: str, *, name: str) -> bytes:
    """Decodifica una llave de 32 bytes en base64 (estándar o URL-safe)."""
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_")
    except (binascii.Error, ValueError) as exc:
        raise ConfigError(f"{name} no es base64 válido") from exc
    if len(raw) != 32:
        raise ConfigError(f"{name} debe tener 32 bytes y tiene {len(raw)}")
    return raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLTERRA_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"

    # --- Base de datos -------------------------------------------------------
    # La aplicación entra con un rol SIN bypass de RLS. Las migraciones entran
    # con el dueño de las tablas (otro rol, otra URL).
    database_url: str = "postgresql+asyncpg://olterra_app:olterra_app@localhost:5432/olterra"
    migrations_database_url: str | None = None

    # --- Mensajería ----------------------------------------------------------
    nats_url: str = "nats://localhost:4222"

    # --- Bóveda --------------------------------------------------------------
    # Llave maestra (KEK) en base64, 32 bytes. Vive fuera de la base de datos.
    master_key: SecretStr | None = None
    master_key_version: int = 1

    # --- Ejecutor ------------------------------------------------------------
    # Grupo de ejecución: "cloud" para el ejecutor de la plataforma; cada agente
    # on-premise (fase 4) tendrá el suyo.
    executor_group: str = "cloud"
    # Llave X25519 con la que el ejecutor abre las credenciales selladas.
    executor_private_key: SecretStr | None = None
    # La nube sella las credenciales con esta llave pública.
    executor_public_key: str | None = None
    executor_max_sessions: int = 32

    # --- Plan de direcciones del túnel (ver olterra.tunnel.addressing) -------
    tunnel_platform_prefix: str = "198.18.0.0/24"
    tunnel_peer_pool: str = "198.18.0.0/16"
    tunnel_peer_block_prefixlen: int = 28
    tunnel_nat_pool: str = "198.19.0.0/16"
    tunnel_nat_block_prefixlen: int = 26
    tunnel_hub_host: str | None = None
    tunnel_hub_port: int = 13231
    tunnel_hub_public_key: str | None = None
    tunnel_trap_receiver: str = "198.18.0.2"
    # SSTP para RouterOS v6 (sin WireGuard). La CA es pública: la del concentrador, en PEM o
    # solo su base64 en una línea (así cabe en .env). Sin ella no se dan de alta routers v6.
    tunnel_sstp_port: int = 4443
    tunnel_sstp_ca: str | None = None

    @model_validator(mode="after")
    def _prod_requires_secrets(self) -> Settings:
        if self.env == "prod":
            missing = [
                name
                for name, value in (
                    ("OLTERRA_MASTER_KEY", self.master_key),
                    ("OLTERRA_EXECUTOR_PUBLIC_KEY", self.executor_public_key),
                )
                if not value
            ]
            if missing:
                raise ConfigError("En producción faltan: " + ", ".join(missing))
        return self

    def master_key_bytes(self) -> bytes:
        if self.master_key is None:
            raise ConfigError("Falta OLTERRA_MASTER_KEY (32 bytes en base64)")
        return decode_key(self.master_key.get_secret_value(), name="OLTERRA_MASTER_KEY")

    def effective_migrations_url(self) -> str:
        return self.migrations_database_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
