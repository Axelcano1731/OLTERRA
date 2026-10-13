"""Mapa FTTH: cajas (NAP, CEO, ODF) con sus puertos, postes y cables.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13

- ``map_boxes``: una caja en el mapa (punto PostGIS, SRID 4326), su tipo, cuántos puertos tiene,
  el splitter y de qué OLT y PON sale. ``olts.location`` ya existía (0001) para ubicar la OLT.
- ``map_box_ports``: qué ONU (cliente) está en cada puerto de una caja NAP. Una ONU va en un solo
  puerto: es el vínculo ONU ↔ puerto NAP del plan.
- ``map_poles``: postes, con su dueño (para conciliar el arriendo con la eléctrica).
- ``map_cables``: cables como líneas; la longitud se calcula sobre el elipsoide al leer.

Todas con RLS + FORCE, su política y FK compuestas ``(tenant_id, id)``.
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"
TABLES = ("map_boxes", "map_box_ports", "map_poles", "map_cables")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE map_boxes (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            kind        text NOT NULL CHECK (kind IN ('nap', 'ceo', 'odf')),
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            location    geometry(Point, 4326) NOT NULL,
            ports       smallint NOT NULL DEFAULT 8 CHECK (ports BETWEEN 0 AND 144),
            splitter    text CHECK (splitter ~ '^1:(2|4|8|16|32|64)$'),
            olt_id      uuid,
            pon         smallint CHECK (pon BETWEEN 1 AND 16),
            notes       text CHECK (length(notes) <= 500),
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE SET NULL (olt_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_map_boxes_location ON map_boxes USING gist (location)")
    op.execute(
        """
        CREATE TABLE map_box_ports (
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            box_id      uuid NOT NULL,
            port        smallint NOT NULL CHECK (port BETWEEN 1 AND 144),
            olt_id      uuid NOT NULL,
            pon         smallint NOT NULL CHECK (pon BETWEEN 1 AND 16),
            onu         smallint NOT NULL CHECK (onu BETWEEN 1 AND 128),
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, box_id, port),
            UNIQUE (tenant_id, olt_id, pon, onu),
            FOREIGN KEY (tenant_id, box_id) REFERENCES map_boxes (tenant_id, id) ON DELETE CASCADE,
            FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE map_poles (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            name        text CHECK (length(name) <= 60),
            location    geometry(Point, 4326) NOT NULL,
            owner       text CHECK (length(owner) <= 60),
            notes       text CHECK (length(notes) <= 500),
            created_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_map_poles_location ON map_poles USING gist (location)")
    op.execute(
        """
        CREATE TABLE map_cables (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            fibers      smallint NOT NULL DEFAULT 12 CHECK (fibers BETWEEN 1 AND 288),
            path        geometry(LineString, 4326) NOT NULL,
            notes       text CHECK (length(notes) <= 500),
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_map_cables_path ON map_cables USING gist (path)")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = olterra_current_tenant())"
            " WITH CHECK (tenant_id = olterra_current_tenant())"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
