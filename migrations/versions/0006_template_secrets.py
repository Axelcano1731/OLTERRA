"""Claves de las cuentas de la ONU en el plan de servicio.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08

Un plan puede poner el usuario de administración de la ONU (y la cuenta normal del cliente).
Los usuarios van en ``body``; sus claves, en ``secrets``: JSON cifrado con la llave del ISP
(``security/vault.py``, AAD ``provision_template:<id>``). La API nunca las devuelve: solo dice si
están guardadas. La tabla ya tiene RLS + FORCE y su política (0004).
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE provision_templates ADD COLUMN secrets bytea")


def downgrade() -> None:
    op.execute("ALTER TABLE provision_templates DROP COLUMN secrets")
