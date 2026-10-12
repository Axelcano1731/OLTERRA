"""Respaldos: el texto que se guarda, cuánto cambió y la diferencia que ve la interfaz."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from olterra.api.backups import change_counts, diff_lines, normalize, open_content, seal_content
from olterra.db.models import OltBackup
from olterra.security.vault import VaultError

OLD = "hostname OLT\nuser add admin login-password ******\ninterface gpon 0/1\n onu 1 desc ANA\n"
NEW = "hostname OLT\nuser add admin login-password ******\ninterface gpon 0/1\n onu 1 desc ANA_P\n onu 2 desc LUIS\n"


def test_normalize_ignores_terminal_noise() -> None:
    assert normalize("\r\n\r\nhostname OLT   \r\n!\r\n\r\n") == "hostname OLT\n!\n"
    assert normalize("a\nb") == normalize("a  \nb\n\n")


def test_change_counts() -> None:
    assert change_counts(OLD, OLD) == (0, 0)
    assert change_counts(OLD, NEW) == (2, 1)  # una línea cambió y otra se agregó
    assert change_counts("", NEW) == (5, 0)


def test_diff_lines_for_the_interface() -> None:
    lines = diff_lines(OLD, NEW)
    assert lines[0]["kind"] == "@"
    assert {"kind": "-", "text": " onu 1 desc ANA"} in lines
    assert {"kind": "+", "text": " onu 1 desc ANA_P"} in lines
    assert {"kind": "+", "text": " onu 2 desc LUIS"} in lines
    assert {"kind": " ", "text": "interface gpon 0/1"} in lines
    assert diff_lines(OLD, OLD) == []


def test_content_is_compressed_and_bound_to_its_backup() -> None:
    tenant, dek = uuid4(), os.urandom(32)
    backup = OltBackup(id=uuid4(), tenant_id=tenant)
    text = NEW * 200
    backup.content = seal_content(tenant, dek, backup.id, text)
    assert b"hostname" not in backup.content
    assert len(backup.content) < len(text) // 10  # comprimido
    assert open_content(tenant, dek, backup) == text
    # Copiar el contenido cifrado a otro respaldo no sirve: el id va en el cifrado.
    other = OltBackup(id=uuid4(), tenant_id=tenant, content=backup.content)
    with pytest.raises(VaultError):
        open_content(tenant, dek, other)
