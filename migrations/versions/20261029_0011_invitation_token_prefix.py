"""Add safe invitation token prefix.

Revision ID: 20261029_0011
Revises: 20261029_0010
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0011"
down_revision: str | None = "20261029_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_invitations",
        sa.Column("token_prefix", sa.String(length=24), nullable=True),
    )
    # Invitations are not public before V3. Invalidating development rows avoids
    # fabricating a prefix for a raw token that was never retained.
    op.execute(sa.text("DELETE FROM organization_invitations"))
    op.alter_column("organization_invitations", "token_prefix", nullable=False)
    op.create_index(
        op.f("ix_organization_invitations_token_prefix"),
        "organization_invitations",
        ["token_prefix"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_organization_invitations_token_prefix"),
        table_name="organization_invitations",
    )
    op.drop_column("organization_invitations", "token_prefix")
