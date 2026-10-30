"""El admin del ISP maneja sus usuarios, el nombre del ISP y el nombre de cada cliente.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-30

- ``users``: la API puede crear usuarios (antes solo ``olterra-admin``). El usuario sigue
  siendo único en toda la plataforma (``uq_users_username``) y todo pasa por RLS.
- ``tenants.name``: el nombre que se muestra del ISP (el "demo" de Tocaima es Colombianet). El
  ``slug`` no cambia: va en los scripts de los MikroTik y en el concentrador.
- ``onu_status.customer_name``: el nombre del cliente que se pone desde el panel. El sondeo no
  lo pisa (escribe ``description``, la de la OLT) y se borra si en esa posición aparece otra
  ONU (otro serial).
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = "olterra_app"


def upgrade() -> None:
    op.execute(f"GRANT INSERT ON users TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (name) ON tenants TO {APP_ROLE}")
    op.execute(
        "ALTER TABLE onu_status ADD COLUMN customer_name text"
        " CHECK (char_length(customer_name) BETWEEN 1 AND 64)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE onu_status DROP COLUMN IF EXISTS customer_name")
    op.execute(f"REVOKE UPDATE (name) ON tenants FROM {APP_ROLE}")
    op.execute(f"REVOKE INSERT ON users FROM {APP_ROLE}")
