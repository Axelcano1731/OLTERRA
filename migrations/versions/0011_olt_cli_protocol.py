"""Acceso a la CLI de la OLT por SSH o Telnet.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-27

Algunas OLT VSOL (la de 8 PON de Tocaima, V1.1.9R) aceptan por SSH solo a los usuarios
internos del sistema, no a los de la CLI: con ellas Olterra entra por Telnet. Telnet viaja en
claro solo entre el MikroTik del ISP y la OLT; hasta el MikroTik va dentro del túnel.

- ``cli_protocol``: ``ssh`` (por defecto) o ``telnet``.
- ``telnet_port``: puerto Telnet de la OLT (23).
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE olts ADD COLUMN cli_protocol text NOT NULL DEFAULT 'ssh'"
        " CHECK (cli_protocol IN ('ssh', 'telnet'))"
    )
    op.execute(
        "ALTER TABLE olts ADD COLUMN telnet_port integer NOT NULL DEFAULT 23"
        " CHECK (telnet_port BETWEEN 1 AND 65535)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE olts DROP COLUMN IF EXISTS telnet_port")
    op.execute("ALTER TABLE olts DROP COLUMN IF EXISTS cli_protocol")
