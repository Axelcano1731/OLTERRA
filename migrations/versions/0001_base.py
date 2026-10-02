"""Esquema base: tenants, bóveda, túnel, OLT, ONU, planes, bitácora y conciliación, con RLS.

Revision ID: 0001
Revises:
Create Date: 2026-10-01

Decisiones que vale la pena leer antes de tocar esto:

- Toda tabla con ``tenant_id`` tiene ENABLE + FORCE ROW LEVEL SECURITY y una
  política que compara con ``olterra_current_tenant()``. Sin tenant fijado, la
  función da NULL y no se ve nada.
- Las llaves foráneas NO respetan RLS: un INSERT con el UUID de un router de otro
  tenant pasaría la FK aunque la aplicación no pueda verlo. Por eso las FK entre
  tablas de tenant son compuestas ``(tenant_id, x_id)``: la fila referida tiene que
  ser del mismo tenant.
- ``audit_log`` es solo-anexar para la aplicación: sin UPDATE ni DELETE.
- La aplicación entra como ``olterra_app`` (sin BYPASSRLS). Ese rol lo crea el
  arranque de la base (``deploy/postgres/init.sql``), no esta migración.
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"

TENANT_TABLES = (
    "tenant_keys",
    "api_keys",
    "credentials",
    "tunnel_routers",
    "olts",
    "onus",
    "plan_runs",
    "audit_log",
    "reconciliation_runs",
)

SCHEMA = r"""
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE FUNCTION olterra_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT NULLIF(current_setting('olterra.tenant_id', true), '')::uuid $$;

CREATE SEQUENCE tenant_net_index_seq START 1;

CREATE TABLE tenants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,31}$'),
    name        text NOT NULL,
    net_index   integer NOT NULL UNIQUE DEFAULT nextval('tenant_net_index_seq'),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'closed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
ALTER SEQUENCE tenant_net_index_seq OWNED BY tenants.net_index;

CREATE TABLE tenant_keys (
    tenant_id    uuid PRIMARY KEY REFERENCES tenants (id) ON DELETE CASCADE,
    wrapped_dek  bytea NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    rotated_at   timestamptz
);

CREATE TABLE api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name          text NOT NULL,
    secret_hash   bytea NOT NULL,
    scopes        text[] NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
CREATE INDEX ix_api_keys_tenant_id ON api_keys (tenant_id);

CREATE TABLE credentials (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('olt', 'routeros', 'snmp')),
    label       text,
    ciphertext  bytea NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    rotated_at  timestamptz,
    UNIQUE (tenant_id, id)
);

CREATE TABLE tunnel_routers (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name               text NOT NULL,
    peer_index         smallint NOT NULL CHECK (peer_index >= 1),
    overlay_ip         inet NOT NULL UNIQUE,
    wg_public_key      text NOT NULL,
    routeros_version   text,
    last_handshake_at  timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, peer_index)
);

CREATE TABLE olts (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    name           text NOT NULL,
    driver         text NOT NULL DEFAULT 'vsol-gpon',
    model          text,
    firmware       text,
    router_id      uuid,
    real_ip        inet,
    nat_index      smallint CHECK (nat_index >= 0),
    nat_ip         inet UNIQUE,
    ssh_port       integer NOT NULL DEFAULT 22,
    snmp_port      integer NOT NULL DEFAULT 161,
    ssh_host_key   text,
    credential_id  uuid,
    status         text NOT NULL DEFAULT 'pending',
    capabilities   jsonb NOT NULL DEFAULT '{}',
    location       geometry(Point, 4326),
    last_seen_at   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, nat_index),
    FOREIGN KEY (tenant_id, router_id) REFERENCES tunnel_routers (tenant_id, id) ON DELETE SET NULL (router_id),
    FOREIGN KEY (tenant_id, credential_id) REFERENCES credentials (tenant_id, id) ON DELETE SET NULL (credential_id)
);
CREATE INDEX ix_olts_location ON olts USING gist (location);

CREATE TABLE onus (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    olt_id        uuid NOT NULL,
    pon           smallint NOT NULL,
    onu           smallint NOT NULL,
    serial        text,
    model         text,
    description   text,
    phase         text,
    rx_dbm        numeric(5, 2),
    tx_dbm        numeric(5, 2),
    olt_rx_dbm    numeric(5, 2),
    distance_m    integer,
    customer_ref  text,
    last_seen_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (olt_id, pon, onu),
    FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_onus_tenant_serial ON onus (tenant_id, serial);

CREATE TABLE plan_runs (
    id            uuid PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    olt_id        uuid NOT NULL,
    requested_by  text NOT NULL,
    priority      smallint NOT NULL,
    access        text NOT NULL CHECK (access IN ('read', 'write')),
    calls         jsonb NOT NULL,
    commands      text[] NOT NULL,
    status        text NOT NULL DEFAULT 'queued',
    result        jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_plan_runs_tenant_created ON plan_runs (tenant_id, created_at DESC);

CREATE TABLE audit_log (
    id           bigserial PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    actor        text NOT NULL,
    action       text NOT NULL,
    target_type  text,
    target_id    text,
    before       jsonb,
    after        jsonb,
    source_ip    inet,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_log_tenant_created ON audit_log (tenant_id, created_at DESC);

CREATE TABLE reconciliation_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    requested_by  text NOT NULL,
    counts        jsonb NOT NULL,
    findings      jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_reconciliation_runs_tenant_created ON reconciliation_runs (tenant_id, created_at DESC);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
    USING (id = olterra_current_tenant())
    WITH CHECK (id = olterra_current_tenant());
"""

GRANTS = f"""
GRANT USAGE ON SCHEMA public TO {APP_ROLE};
GRANT EXECUTE ON FUNCTION olterra_current_tenant() TO {APP_ROLE};
GRANT SELECT ON tenants, tenant_keys TO {APP_ROLE};
GRANT SELECT, UPDATE (last_used_at) ON api_keys TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE
    ON credentials, tunnel_routers, olts, onus, plan_runs, reconciliation_runs TO {APP_ROLE};
GRANT SELECT, INSERT ON audit_log TO {APP_ROLE};
GRANT USAGE ON SEQUENCE audit_log_id_seq TO {APP_ROLE};
"""


def _statements(sql: str) -> list[str]:
    # asyncpg prepara cada sentencia: no acepta varias en un solo execute.
    # Ninguna sentencia de este archivo tiene ";" adentro (ni las funciones).
    return [part.strip() for part in sql.split(";") if part.strip()]


def upgrade() -> None:
    for statement in _statements(SCHEMA):
        op.execute(statement)
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = olterra_current_tenant())"
            " WITH CHECK (tenant_id = olterra_current_tenant())"
        )
    for statement in _statements(GRANTS):
        op.execute(statement)


def downgrade() -> None:
    for table in (*reversed(TENANT_TABLES), "tenants"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS olterra_current_tenant()")
