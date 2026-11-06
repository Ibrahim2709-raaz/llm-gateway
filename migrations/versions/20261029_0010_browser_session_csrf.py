"""Add CSRF binding to browser sessions.

Revision ID: 20261029_0010
Revises: 20261029_0009
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0010"
down_revision: str | None = "20261029_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # V3 browser sessions have not shipped yet. Invalidating any development
    # sessions is safer than inventing a CSRF secret that no browser possesses.
    op.add_column(
        "browser_sessions",
        sa.Column("csrf_token_hash", sa.String(length=128), nullable=True),
    )
    op.execute(sa.text("DELETE FROM browser_sessions"))
    op.alter_column("browser_sessions", "csrf_token_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("browser_sessions", "csrf_token_hash")
