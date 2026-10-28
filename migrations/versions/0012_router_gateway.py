"""El MikroTik del ISP como puerta hacia sus OLT.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-28

Antes cada OLT necesitaba su regla NAT en el MikroTik y su IP en el concentrador: agregar una
OLT obligaba a rotar el script del router y pegar otro bloque en el CHR. Ahora el script crea
en el MikroTik un usuario ``olterra`` que solo entra por SSH desde el túnel y no puede cambiar
nada; el ejecutor entra con él y desde ahí abre la conexión a la IP real de cada OLT (reenvío
SSH). Ninguna OLT nueva toca el router ni el concentrador.

- ``gateway_credential_id``: usuario y clave de esa puerta (cifrados como los de las OLT). Sin
  ella, el router tiene un script viejo y sus OLT siguen por NAT 1:1.
- ``ssh_port``: puerto del SSH del MikroTik (22).
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tunnel_routers ADD COLUMN gateway_credential_id uuid")
    op.execute(
        "ALTER TABLE tunnel_routers ADD CONSTRAINT tunnel_routers_gateway_credential_fk"
        " FOREIGN KEY (tenant_id, gateway_credential_id) REFERENCES credentials (tenant_id, id)"
        " ON DELETE SET NULL (gateway_credential_id)"
    )
    op.execute(
        "ALTER TABLE tunnel_routers ADD COLUMN ssh_port integer NOT NULL DEFAULT 22"
        " CHECK (ssh_port BETWEEN 1 AND 65535)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tunnel_routers DROP COLUMN IF EXISTS ssh_port")
    op.execute(
        "ALTER TABLE tunnel_routers DROP CONSTRAINT IF EXISTS tunnel_routers_gateway_credential_fk"
    )
    op.execute("ALTER TABLE tunnel_routers DROP COLUMN IF EXISTS gateway_credential_id")
