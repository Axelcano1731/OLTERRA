"""Túnel SSTP para RouterOS v6: cada router guarda su transporte y, si es SSTP, su usuario PPP.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02

- ``transport``: ``wireguard`` (RouterOS 7) o ``sstp`` (RouterOS 6, que no tiene WireGuard).
- ``ppp_user``: el usuario del secreto PPP en el concentrador. Único entre todos los ISP
  (el concentrador es uno solo); lleva el tenant en el nombre, así no chocan.
- ``wg_public_key`` deja de ser obligatoria: un router SSTP no tiene. Cada transporte exige
  su credencial (``ck_tunnel_routers_credential``).

La clave PPP no se guarda, como la llave privada WireGuard: queda en el router y en el
secreto del concentrador. La tabla ya tiene RLS + FORCE y su política desde 0001.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tunnel_routers
            ADD COLUMN transport text NOT NULL DEFAULT 'wireguard'
                CONSTRAINT ck_tunnel_routers_transport CHECK (transport IN ('wireguard', 'sstp')),
            ADD COLUMN ppp_user text,
            ALTER COLUMN wg_public_key DROP NOT NULL,
            ADD CONSTRAINT ck_tunnel_routers_credential CHECK (
                (transport = 'wireguard' AND wg_public_key IS NOT NULL)
                OR (transport = 'sstp' AND ppp_user IS NOT NULL)
            )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_tunnel_routers_ppp_user ON tunnel_routers (ppp_user)")


def downgrade() -> None:
    # Falla a propósito si quedan routers SSTP: no hay cómo volverlos WireGuard sin su llave.
    op.execute("DROP INDEX uq_tunnel_routers_ppp_user")
    op.execute(
        """
        ALTER TABLE tunnel_routers
            DROP CONSTRAINT ck_tunnel_routers_credential,
            ALTER COLUMN wg_public_key SET NOT NULL,
            DROP COLUMN ppp_user,
            DROP COLUMN transport
        """
    )
