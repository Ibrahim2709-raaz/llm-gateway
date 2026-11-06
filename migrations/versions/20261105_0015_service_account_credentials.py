"""Link gateway credentials to organization service accounts.

Revision ID: 20261105_0015
Revises: 20261029_0014
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0015"
down_revision: str | None = "20261029_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("service_account_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_api_keys_service_account_id"),
        "api_keys",
        ["service_account_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_api_keys_service_account_id_service_accounts"),
        "api_keys",
        "service_accounts",
        ["service_account_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_api_keys_service_account_id_service_accounts"),
        "api_keys",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_api_keys_service_account_id"), table_name="api_keys")
    op.drop_column("api_keys", "service_account_id")
