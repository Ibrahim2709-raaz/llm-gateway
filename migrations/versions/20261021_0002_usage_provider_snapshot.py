"""Add provider snapshots to usage records.

Revision ID: 20261021_0002
Revises: 20261010_0001
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_0002"
down_revision: str | None = "20261010_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_records", sa.Column("provider", sa.String(length=64), nullable=True))
    op.add_column("usage_records", sa.Column("model", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_records", "model")
    op.drop_column("usage_records", "provider")
