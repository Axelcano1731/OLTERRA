"""Velocidad de cada ONU cada 5 minutos (como MRTG) y la velocidad de su plan.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03

- ``onu_rates``: la bajada y la subida promedio (bit/s) entre dos lecturas de tráfico, una fila
  por lectura (cada 5 minutos). Se borra a los ``OLTERRA_MONITOR_RATE_RETENTION_DAYS`` (8): para
  el mes se usan los promedios por hora de ``onu_traffic``.
- ``onu_status.plan_mbps``: la velocidad del plan del cliente, puesta en la ficha (la OLT no la
  sabe: el límite lo pone el MikroTik). Se borra, como el nombre, si en esa posición aparece otra
  ONU.
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE onu_status ADD COLUMN plan_mbps integer CHECK (plan_mbps BETWEEN 1 AND 10000)"
    )
    op.execute(
        """
        CREATE TABLE onu_rates (
            tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id     uuid NOT NULL,
            pon        smallint NOT NULL,
            onu        smallint NOT NULL,
            at         timestamptz NOT NULL,
            down_bps   bigint NOT NULL CHECK (down_bps >= 0),
            up_bps     bigint NOT NULL CHECK (up_bps >= 0),
            PRIMARY KEY (tenant_id, olt_id, pon, onu, at),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_onu_rates_at ON onu_rates (at)")
    op.execute("ALTER TABLE onu_rates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE onu_rates FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON onu_rates"
        " USING (tenant_id = olterra_current_tenant())"
        " WITH CHECK (tenant_id = olterra_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON onu_rates TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS onu_rates")
    op.execute("ALTER TABLE onu_status DROP COLUMN IF EXISTS plan_mbps")
