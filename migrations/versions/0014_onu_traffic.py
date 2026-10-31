"""Consumo de cada ONU: contadores de tráfico de la OLT, sumados por hora.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-31

- ``onu_status``: los últimos contadores que dio la OLT (``down_bytes``/``up_bytes``, acumulados
  desde que la ONU se registró), cuándo se leyeron y el promedio de bajada y subida entre las dos
  últimas lecturas (``down_bps``/``up_bps``).
- ``onu_traffic``: lo que bajó y subió cada ONU en cada hora, y el pico del promedio de 15 minutos
  en esa hora. Una fila por ONU y hora, no una por lectura (la base no crece con cada sondeo);
  se borra a los ``OLTERRA_MONITOR_TRAFFIC_RETENTION_DAYS`` (62).
- ``olts.traffic_requested_at``: cuándo se pidió la última lectura de tráfico. Va en el sondeo
  cada ``OLTERRA_MONITOR_TRAFFIC_EVERY_MINUTES`` (15), no en todos: en la HSGQ es un comando
  por ONU.
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    for column in ("down_bytes", "up_bytes", "down_bps", "up_bps"):
        op.execute(f"ALTER TABLE onu_status ADD COLUMN {column} bigint CHECK ({column} >= 0)")
    op.execute("ALTER TABLE onu_status ADD COLUMN traffic_at timestamptz")
    op.execute("ALTER TABLE olts ADD COLUMN traffic_requested_at timestamptz")
    op.execute(
        """
        CREATE TABLE onu_traffic (
            tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id         uuid NOT NULL,
            pon            smallint NOT NULL,
            onu            smallint NOT NULL,
            hour           timestamptz NOT NULL CHECK (date_trunc('hour', hour) = hour),
            down_bytes     bigint NOT NULL DEFAULT 0 CHECK (down_bytes >= 0),
            up_bytes       bigint NOT NULL DEFAULT 0 CHECK (up_bytes >= 0),
            peak_down_bps  bigint NOT NULL DEFAULT 0 CHECK (peak_down_bps >= 0),
            peak_up_bps    bigint NOT NULL DEFAULT 0 CHECK (peak_up_bps >= 0),
            PRIMARY KEY (tenant_id, olt_id, pon, onu, hour),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_onu_traffic_hour ON onu_traffic (hour)")
    op.execute("ALTER TABLE onu_traffic ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE onu_traffic FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON onu_traffic"
        " USING (tenant_id = olterra_current_tenant())"
        " WITH CHECK (tenant_id = olterra_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON onu_traffic TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS onu_traffic")
    op.execute("ALTER TABLE olts DROP COLUMN IF EXISTS traffic_requested_at")
    for column in ("traffic_at", "up_bps", "down_bps", "up_bytes", "down_bytes"):
        op.execute(f"ALTER TABLE onu_status DROP COLUMN IF EXISTS {column}")
