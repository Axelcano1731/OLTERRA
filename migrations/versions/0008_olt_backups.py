"""Respaldos de la configuración de la OLT.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12

Cada respaldo es lo que devuelve ``show running-config``, con las claves tapadas por el
ejecutor (las claves nunca viajan en claro por NATS) y además cifrado con la llave del ISP
(``security/vault.py``, AAD ``olt_backup:<id>``) y comprimido: es la red entera del ISP, con
nombres de clientes. ``sha256`` (del texto) dice si cambió respecto al respaldo anterior sin
descifrar nada.

``olterra_due_backups(interval)`` es el reloj: devuelve, sin ver datos de nadie, las OLT que ya
respondieron alguna vez y no tienen un respaldo en ese intervalo (ni uno en curso).
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE olt_backups (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            olt_id        uuid NOT NULL,
            plan_id       uuid,
            trigger       text NOT NULL CHECK (trigger IN ('auto', 'manual')),
            status        text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'ok', 'failed')),
            requested_by  text NOT NULL,
            content       bytea,
            sha256        text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            size_bytes    integer,
            line_count    integer,
            changed       boolean,
            added         integer,
            removed       integer,
            error         text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            finished_at   timestamptz,
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_olt_backups_olt ON olt_backups (tenant_id, olt_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_olt_backups_plan ON olt_backups (plan_id) WHERE plan_id IS NOT NULL"
    )
    op.execute("ALTER TABLE olt_backups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE olt_backups FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON olt_backups"
        " USING (tenant_id = olterra_current_tenant())"
        " WITH CHECK (tenant_id = olterra_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON olt_backups TO {APP_ROLE}")

    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    op.execute(
        f"""
        CREATE FUNCTION olterra_due_backups(p_every interval)
        RETURNS TABLE (tenant_id uuid, olt_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = "{schema}", pg_temp
        AS $$
            SELECT o.tenant_id, o.id
            FROM olts o
            JOIN tenants t ON t.id = o.tenant_id AND t.status = 'active'
            WHERE o.credential_id IS NOT NULL
              AND o.last_seen_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM olt_backups b
                  WHERE b.tenant_id = o.tenant_id AND b.olt_id = o.id
                    AND (b.created_at > now() - p_every
                         OR (b.status = 'pending' AND b.created_at > now() - interval '1 hour'))
              )
            ORDER BY o.last_seen_at
            LIMIT 20
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION olterra_due_backups(interval) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION olterra_due_backups(interval) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION olterra_due_backups(interval)")
    op.execute("DROP TABLE olt_backups")
