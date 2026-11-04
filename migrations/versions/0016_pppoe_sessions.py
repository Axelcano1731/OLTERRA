"""Sesiones PPPoE del MikroTik del ISP y el usuario PPPoE de cada ONU.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04

- ``pppoe_sessions``: las sesiones PPPoE activas de cada MikroTik (usuario, MAC, IP, tiempo
  conectado, contadores y el plan de su cola), como las leyó Olterra en su último sondeo del
  router. Se reescribe en cada sondeo: una sesión que ya no está se borra.
- ``onu_status.pppoe_user`` y ``pppoe_user_source``: con qué usuario PPPoE navega la ONU. Lo pone
  Olterra si el nombre del cliente y el usuario coinciden (``nombre``) o alguien en la ficha
  (``manual``, que el sondeo no pisa). Con él, la ficha muestra la sesión y, en las OLT que no dan
  el tráfico por ONU (BCD), la velocidad y el consumo salen del MikroTik.
- ``tunnel_routers.monitor_requested_at`` y ``monitor_plan_id``: el último sondeo del router.
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE onu_status ADD COLUMN pppoe_user text CHECK (char_length(pppoe_user) <= 64)"
    )
    op.execute(
        "ALTER TABLE onu_status ADD COLUMN pppoe_user_source text"
        " CHECK (pppoe_user_source IN ('nombre', 'manual'))"
    )
    op.execute("ALTER TABLE tunnel_routers ADD COLUMN monitor_requested_at timestamptz")
    op.execute("ALTER TABLE tunnel_routers ADD COLUMN monitor_plan_id uuid")
    op.execute(
        f"GRANT UPDATE (monitor_requested_at, monitor_plan_id) ON tunnel_routers TO {APP_ROLE}"
    )
    op.execute(
        """
        CREATE TABLE pppoe_sessions (
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            router_id       uuid NOT NULL,
            pppoe_user      text NOT NULL,
            mac             text,
            address         text,
            uptime          text,
            down_bytes      bigint,
            up_bytes        bigint,
            plan_down_bps   bigint,
            plan_up_bps     bigint,
            seen_at         timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, router_id, pppoe_user),
            FOREIGN KEY (tenant_id, router_id) REFERENCES tunnel_routers (tenant_id, id)
                ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE pppoe_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE pppoe_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON pppoe_sessions"
        " USING (tenant_id = olterra_current_tenant())"
        " WITH CHECK (tenant_id = olterra_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON pppoe_sessions TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pppoe_sessions")
    op.execute(
        f"REVOKE UPDATE (monitor_requested_at, monitor_plan_id) ON tunnel_routers FROM {APP_ROLE}"
    )
    op.execute("ALTER TABLE tunnel_routers DROP COLUMN IF EXISTS monitor_plan_id")
    op.execute("ALTER TABLE tunnel_routers DROP COLUMN IF EXISTS monitor_requested_at")
    op.execute("ALTER TABLE onu_status DROP COLUMN IF EXISTS pppoe_user_source")
    op.execute("ALTER TABLE onu_status DROP COLUMN IF EXISTS pppoe_user")
