"""Add hashed browser session and user action tokens.

Revision ID: 20261029_0009
Revises: 20261029_0008
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0009"
down_revision: str | None = "20261029_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("active_organization_id", sa.Uuid(), nullable=True),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_organization_id"],
            ["organizations.id"],
            name=op.f("fk_browser_sessions_active_organization_id_organizations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_browser_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_browser_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_browser_sessions_token_hash")),
    )
    op.create_index(
        op.f("ix_browser_sessions_active_organization_id"),
        "browser_sessions",
        ["active_organization_id"],
    )
    op.create_index(op.f("ix_browser_sessions_expires_at"), "browser_sessions", ["expires_at"])
    op.create_index(op.f("ix_browser_sessions_revoked_at"), "browser_sessions", ["revoked_at"])
    op.create_index(
        op.f("ix_browser_sessions_token_prefix"), "browser_sessions", ["token_prefix"]
    )
    op.create_index(op.f("ix_browser_sessions_user_id"), "browser_sessions", ["user_id"])

    op.create_table(
        "user_action_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("token_prefix", sa.String(length=24), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('email_verification', 'password_reset')",
            name=op.f("ck_user_action_tokens_user_action_token_purpose"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_action_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_action_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_action_tokens_token_hash")),
    )
    op.create_index(
        op.f("ix_user_action_tokens_consumed_at"),
        "user_action_tokens",
        ["consumed_at"],
    )
    op.create_index(
        op.f("ix_user_action_tokens_expires_at"),
        "user_action_tokens",
        ["expires_at"],
    )
    op.create_index(op.f("ix_user_action_tokens_purpose"), "user_action_tokens", ["purpose"])
    op.create_index(
        op.f("ix_user_action_tokens_token_prefix"),
        "user_action_tokens",
        ["token_prefix"],
    )
    op.create_index(op.f("ix_user_action_tokens_user_id"), "user_action_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_action_tokens")
    op.drop_table("browser_sessions")
