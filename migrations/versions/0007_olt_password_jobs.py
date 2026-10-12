"""Trabajos de cambio de clave de la OLT.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-11

Cambiar la clave con la que Olterra entra a una OLT son varios planes (poner la clave nueva sin
guardar, entrar con ella en otra sesión y guardar, y solo entonces actualizar la bóveda), así que
usa el mismo motor que el alta (``api/jobs.py``): un ``provision_jobs`` de tipo ``olt_password``.
Las claves nuevas van en ``secrets`` cifradas con la llave del ISP y se borran al terminar. La
tabla ya tiene RLS + FORCE y su política (0005).
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE provision_jobs DROP CONSTRAINT provision_jobs_kind_check")
    op.execute(
        "ALTER TABLE provision_jobs ADD CONSTRAINT provision_jobs_kind_check"
        " CHECK (kind IN ('authorize', 'configure', 'olt_password'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM provision_jobs WHERE kind = 'olt_password'")
    op.execute("ALTER TABLE provision_jobs DROP CONSTRAINT provision_jobs_kind_check")
    op.execute(
        "ALTER TABLE provision_jobs ADD CONSTRAINT provision_jobs_kind_check"
        " CHECK (kind IN ('authorize', 'configure'))"
    )
