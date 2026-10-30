"""Store routing policy version on decision snapshots.

Revision ID: 20261029_0012
Revises: 20261029_0011
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0012"
down_revision: str | None = "20261029_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "routing_decision_snapshots",
        sa.Column(
            "policy_version",
            sa.String(length=64),
            nullable=False,
            server_default="baseline-v1",
        ),
    )
    op.alter_column("routing_decision_snapshots", "policy_version", server_default=None)


def downgrade() -> None:
    op.drop_column("routing_decision_snapshots", "policy_version")
