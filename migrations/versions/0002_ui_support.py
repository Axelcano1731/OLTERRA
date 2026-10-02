"""Lo que pide la interfaz: historial de consultas por OLT y origen de cada conciliación.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01

- ``plan_runs``: índice para listar las consultas de una OLT, de la más nueva a la más vieja.
- ``reconciliation_runs``: de dónde salió cada corrida (``api``, ``upload`` o ``demo``) y
  con qué archivos. ``files`` guarda solo nombre, tipo y cuántos registros salieron de
  cada archivo; el contenido no se guarda.

Las dos tablas ya tienen RLS + FORCE y su política desde 0001; esto no cambia eso.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_plan_runs_tenant_olt_created ON plan_runs (tenant_id, olt_id, created_at DESC)"
    )
    op.execute(
        """
        ALTER TABLE reconciliation_runs
            ADD COLUMN source text NOT NULL DEFAULT 'api'
                CONSTRAINT ck_reconciliation_runs_source CHECK (source IN ('api', 'upload', 'demo')),
            ADD COLUMN files jsonb NOT NULL DEFAULT '[]'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reconciliation_runs DROP COLUMN files, DROP COLUMN source")
    op.execute("DROP INDEX ix_plan_runs_tenant_olt_created")
