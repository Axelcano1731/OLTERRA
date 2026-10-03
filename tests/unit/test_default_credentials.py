"""Clave de fábrica de VSOL: opcional, nunca se imprime y vacía equivale a no configurada."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from olterra.api.routes.olts import _login
from olterra.api.schemas import OltCreate
from olterra.config import Settings


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_blank_factory_password_is_not_configured() -> None:
    assert _settings(vsol_default_password="").vsol_default_password is None
    assert _settings(vsol_default_password="  ").vsol_default_password is None
    assert _settings().vsol_default_password is None


def test_factory_password_is_masked() -> None:
    settings = _settings(vsol_default_password="Fab@Pass-123#")
    assert "Fab@Pass-123#" not in repr(settings)
    assert "Fab@Pass-123#" not in settings.model_dump_json()


def test_login_uses_factory_credentials_only_when_the_password_is_blank() -> None:
    state = SimpleNamespace(settings=_settings(vsol_default_password="factory"))
    username, password, used = _login(OltCreate(name="A"), state)  # type: ignore[arg-type]
    assert (username, password.get_secret_value(), used) == ("admin", "factory", True)
    own = OltCreate(name="A", username="otro", password="mia")  # type: ignore[arg-type]
    username, password, used = _login(own, state)  # type: ignore[arg-type]
    assert (username, password.get_secret_value(), used) == ("otro", "mia", False)


def test_login_without_factory_password_asks_for_one() -> None:
    state = SimpleNamespace(settings=_settings())
    with pytest.raises(HTTPException) as error:
        _login(OltCreate(name="A"), state)  # type: ignore[arg-type]
    assert error.value.status_code == 400
