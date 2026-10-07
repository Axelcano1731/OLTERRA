"""Usuarios con contraseña, sus sesiones y los trabajos de alta de ONU.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07

- ``users``: usuario (único en toda la plataforma, sin distinguir mayúsculas), hash scrypt de la
  contraseña, rol, cambio obligatorio de contraseña y bloqueo por intentos fallidos.
- ``user_sessions``: solo el SHA-256 del secreto del token, con vencimiento.
- ``provision_jobs``: el alta de una ONU de punta a punta (índice libre, autorizar y guardar,
  esperar a que la ONU se conecte, WAN y WiFi, verificar). Las claves PPPoE y WiFi del cliente
  van cifradas con la DEK del tenant (``secrets``) y se borran al terminar.
- ``olts.pon_ports``: cuántos PON tiene la OLT (se descubre de ``show interface brief``).

Dos funciones ``SECURITY DEFINER`` y nada más, porque hay dos momentos en que todavía no se
sabe el tenant: al entrar (el usuario escribe su nombre, no su ISP) y el reloj que avanza los
trabajos de alta. Las dos devuelven solo identificadores; todo lo demás se lee después con el
tenant fijado y bajo RLS. Solo ``olterra_app`` las puede ejecutar.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"
TABLES = ("users", "user_sessions", "provision_jobs")

SCHEMA = [
    """
    CREATE TABLE users (
        id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id             uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        username              text NOT NULL CHECK (username ~ '^[A-Za-z][A-Za-z0-9_.-]{2,31}$'),
        display_name          text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 80),
        password_hash         text NOT NULL,
        role                  text NOT NULL DEFAULT 'admin'
                              CHECK (role IN ('admin', 'tecnico', 'lectura')),
        must_change_password  boolean NOT NULL DEFAULT true,
        failed_logins         smallint NOT NULL DEFAULT 0,
        locked_until          timestamptz,
        disabled_at           timestamptz,
        last_login_at         timestamptz,
        created_at            timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id)
    )
    """,
    "CREATE UNIQUE INDEX uq_users_username ON users (lower(username))",
    """
    CREATE TABLE user_sessions (
        id            uuid PRIMARY KEY,
        tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        user_id       uuid NOT NULL,
        secret_hash   bytea NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now(),
        expires_at    timestamptz NOT NULL,
        last_used_at  timestamptz,
        revoked_at    timestamptz,
        source_ip     inet,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_user_sessions_user ON user_sessions (tenant_id, user_id)",
    """
    CREATE TABLE provision_jobs (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        olt_id           uuid NOT NULL,
        kind             text NOT NULL CHECK (kind IN ('authorize', 'configure')),
        status           text NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'done', 'failed')),
        step             text NOT NULL,
        template         jsonb NOT NULL,
        template_name    text,
        request          jsonb NOT NULL,
        secrets          bytea,
        detail           jsonb NOT NULL DEFAULT '{}',
        current_plan_id  uuid,
        attempts         smallint NOT NULL DEFAULT 0,
        next_run_at      timestamptz,
        error            text,
        requested_by     text NOT NULL,
        created_at       timestamptz NOT NULL DEFAULT now(),
        updated_at       timestamptz NOT NULL DEFAULT now(),
        finished_at      timestamptz,
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, olt_id) REFERENCES olts (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_provision_jobs_olt ON provision_jobs (tenant_id, olt_id, created_at DESC)",
    "CREATE INDEX ix_provision_jobs_plan ON provision_jobs (current_plan_id)"
    " WHERE current_plan_id IS NOT NULL",
    "CREATE INDEX ix_provision_jobs_due ON provision_jobs (next_run_at) WHERE status = 'running'",
    "ALTER TABLE olts ADD COLUMN pon_ports smallint CHECK (pon_ports BETWEEN 1 AND 16)",
]


def _functions(schema: str) -> list[str]:
    path = f'SET search_path = "{schema}", pg_temp'
    return [
        f"""
        CREATE FUNCTION olterra_login_lookup(p_username text)
        RETURNS TABLE (tenant_id uuid, user_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER {path}
        AS $$
            SELECT u.tenant_id, u.id
            FROM users u JOIN tenants t ON t.id = u.tenant_id
            WHERE lower(u.username) = lower(p_username)
              AND u.disabled_at IS NULL
              AND t.status = 'active'
        $$
        """,
        f"""
        CREATE FUNCTION olterra_due_provision_jobs()
        RETURNS TABLE (tenant_id uuid, job_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER {path}
        AS $$
            SELECT j.tenant_id, j.id
            FROM provision_jobs j
            WHERE j.status = 'running'
              AND ((j.current_plan_id IS NULL AND j.next_run_at <= now())
                   OR j.updated_at < now() - interval '10 minutes')
            ORDER BY j.next_run_at NULLS FIRST
            LIMIT 50
        $$
        """,
        "REVOKE ALL ON FUNCTION olterra_login_lookup(text) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION olterra_due_provision_jobs() FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION olterra_login_lookup(text) TO {APP_ROLE}",
        f"GRANT EXECUTE ON FUNCTION olterra_due_provision_jobs() TO {APP_ROLE}",
    ]


def upgrade() -> None:
    for statement in SCHEMA:
        op.execute(statement)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            " USING (tenant_id = olterra_current_tenant())"
            " WITH CHECK (tenant_id = olterra_current_tenant())"
        )
    # La API cambia contadores de intentos y la contraseña, pero no crea ni borra usuarios:
    # eso es de olterra-admin (rol dueño).
    op.execute(f"GRANT SELECT, UPDATE ON users TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions, provision_jobs TO {APP_ROLE}"
    )
    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    for statement in _functions(schema):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP FUNCTION olterra_due_provision_jobs()")
    op.execute("DROP FUNCTION olterra_login_lookup(text)")
    op.execute("ALTER TABLE olts DROP COLUMN pon_ports")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
