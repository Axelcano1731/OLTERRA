"""Monitoreo: estado de cada ONU, historial de potencia y alarmas.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13

- ``onu_status``: lo último que se sabe de cada ONU (fase, en línea, potencia, cliente), una fila
  por ONU. Lo reescribe cada sondeo.
- ``onu_samples``: historial de potencia. Una muestra por ONU como máximo cada hora, o antes si la
  señal cambió 1 dB o más o la ONU cayó o volvió; se borra a los 30 días. La base es compartida
  (A.13): no se guarda cada sondeo.
- ``alarms``: alarmas abiertas y cerradas. ``pon`` y ``onu`` valen 0 cuando la alarma es de toda
  la OLT o de todo el PON; el índice único parcial deja una sola abierta por cosa y tipo.
- ``olts.monitor_*``: cuándo se pidió el último sondeo y cuál está en curso.

``olterra_due_monitoring(interval)`` es el reloj: devuelve, sin ver datos de nadie, las OLT que ya
respondieron alguna vez y no tienen un sondeo en ese intervalo.
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"
TABLES = ("onu_status", "onu_samples", "alarms")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE onu_status (
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id          uuid NOT NULL,
            pon             smallint NOT NULL CHECK (pon BETWEEN 1 AND 16),
            onu             smallint NOT NULL CHECK (onu BETWEEN 1 AND 128),
            serial          text,
            description     text,
            phase           text,
            online          boolean NOT NULL,
            rx_dbm          numeric(5, 2),
            status_since    timestamptz NOT NULL DEFAULT now(),
            last_online_at  timestamptz,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, olt_id, pon, onu),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE onu_samples (
            tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id     uuid NOT NULL,
            pon        smallint NOT NULL,
            onu        smallint NOT NULL,
            at         timestamptz NOT NULL DEFAULT now(),
            online     boolean NOT NULL,
            rx_dbm     numeric(5, 2),
            PRIMARY KEY (tenant_id, olt_id, pon, onu, at),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_onu_samples_at ON onu_samples (at)")
    op.execute(
        """
        CREATE TABLE alarms (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id        uuid NOT NULL,
            pon           smallint NOT NULL DEFAULT 0,
            onu           smallint NOT NULL DEFAULT 0,
            kind          text NOT NULL CHECK (kind IN (
                              'olt_unreachable', 'pon_down', 'onu_offline', 'rx_low', 'rx_high')),
            severity      text NOT NULL CHECK (severity IN ('critical', 'warning')),
            message       text NOT NULL,
            detail        jsonb NOT NULL DEFAULT '{}',
            started_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at  timestamptz NOT NULL DEFAULT now(),
            cleared_at    timestamptz,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_alarms_open ON alarms (tenant_id, olt_id, pon, onu, kind)"
        " WHERE cleared_at IS NULL"
    )
    op.execute("CREATE INDEX ix_alarms_recent ON alarms (tenant_id, started_at DESC)")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = olterra_current_tenant())"
            " WITH CHECK (tenant_id = olterra_current_tenant())"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")

    op.execute("ALTER TABLE olts ADD COLUMN monitor_requested_at timestamptz")
    op.execute("ALTER TABLE olts ADD COLUMN monitor_plan_id uuid")
    op.execute("ALTER TABLE olts ADD COLUMN monitored_at timestamptz")

    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    op.execute(
        f"""
        CREATE FUNCTION olterra_due_monitoring(p_every interval)
        RETURNS TABLE (tenant_id uuid, olt_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = "{schema}", pg_temp
        AS $$
            SELECT o.tenant_id, o.id
            FROM olts o
            JOIN tenants t ON t.id = o.tenant_id AND t.status = 'active'
            WHERE o.credential_id IS NOT NULL
              AND o.last_seen_at IS NOT NULL
              AND (o.monitor_requested_at IS NULL
                   OR (o.monitor_requested_at <= now() - p_every
                       AND (o.monitor_plan_id IS NULL
                            OR o.monitor_requested_at <= now() - interval '10 minutes')))
            ORDER BY o.monitor_requested_at NULLS FIRST
            LIMIT 50
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION olterra_due_monitoring(interval) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION olterra_due_monitoring(interval) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION olterra_due_monitoring(interval)")
    op.execute("ALTER TABLE olts DROP COLUMN monitored_at")
    op.execute("ALTER TABLE olts DROP COLUMN monitor_plan_id")
    op.execute("ALTER TABLE olts DROP COLUMN monitor_requested_at")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
