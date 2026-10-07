"""Plantillas de aprovisionamiento de ONU.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07

Una plantilla guarda lo común a un plan de servicio (perfiles, T-CONT, GEM, VLAN, WAN PPPoE,
WiFi) en ``body`` (``drivers/vsol_gpon/provisioning.TemplateBody``). No guarda nada de un
cliente ni claves: el usuario PPPoE, el SSID y sus claves llegan en cada alta y las claves solo
viajan selladas al ejecutor.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE provision_templates (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            name        text NOT NULL CHECK (name ~ '^[A-Za-z0-9_. -]{1,48}$'),
            driver      text NOT NULL DEFAULT 'vsol-gpon',
            body        jsonb NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, name)
        )
        """
    )
    op.execute("ALTER TABLE provision_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE provision_templates FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON provision_templates"
        " USING (tenant_id = olterra_current_tenant())"
        " WITH CHECK (tenant_id = olterra_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON provision_templates TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE provision_templates")
