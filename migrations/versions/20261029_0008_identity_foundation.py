"""Add organization identity and audit foundation.

Revision ID: 20261029_0008
Revises: 20261027_0007
Create Date: 2026-10-29
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20261029_0008"
down_revision: str | None = "20261027_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name=op.f("ck_organizations_organization_status"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_organizations_client_id_clients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("client_id", name="uq_organizations_client_id"),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
    )
    op.create_index(op.f("ix_organizations_client_id"), "organizations", ["client_id"])
    op.create_index(op.f("ix_organizations_status"), "organizations", ["status"])

    clients = sa.table(
        "clients",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String(length=200)),
    )
    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Uuid()),
        sa.column("client_id", sa.Uuid()),
        sa.column("slug", sa.String(length=100)),
        sa.column("name", sa.String(length=200)),
        sa.column("status", sa.String(length=32)),
    )
    connection = op.get_bind()
    legacy_clients = connection.execute(sa.select(clients.c.id, clients.c.name)).all()
    if legacy_clients:
        op.bulk_insert(
            organizations,
            [
                {
                    "id": uuid4(),
                    "client_id": client.id,
                    "slug": f"legacy-{client.id}",
                    "name": client.name,
                    "status": "active",
                }
                for client in legacy_clients
            ],
        )

    op.create_table(
        "users",
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("platform_role", sa.String(length=32), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name=op.f("ck_users_user_status"),
        ),
        sa.CheckConstraint(
            "platform_role IS NULL OR platform_role IN ('platform_operator')",
            name=op.f("ck_users_user_platform_role"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email_normalized", name=op.f("uq_users_email_normalized")),
    )
    op.create_index(op.f("ix_users_platform_role"), "users", ["platform_role"])
    op.create_index(op.f("ix_users_status"), "users", ["status"])

    op.create_table(
        "organization_memberships",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'developer', 'billing_viewer')",
            name=op.f("ck_organization_memberships_organization_membership_role"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name=op.f("ck_organization_memberships_organization_membership_status"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f(
                "fk_organization_memberships_organization_id_organizations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_memberships")),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_membership_user"
        ),
    )
    op.create_index(
        op.f("ix_organization_memberships_organization_id"),
        "organization_memberships",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_organization_memberships_status"),
        "organization_memberships",
        ["status"],
    )
    op.create_index(
        op.f("ix_organization_memberships_user_id"),
        "organization_memberships",
        ["user_id"],
    )

    op.create_table(
        "organization_invitations",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'developer', 'billing_viewer')",
            name=op.f("ck_organization_invitations_organization_invitation_role"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_organization_invitations_invited_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f(
                "fk_organization_invitations_organization_id_organizations"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_organization_invitations_token_hash")),
    )
    op.create_index(
        op.f("ix_organization_invitations_email_normalized"),
        "organization_invitations",
        ["email_normalized"],
    )
    op.create_index(
        op.f("ix_organization_invitations_invited_by_user_id"),
        "organization_invitations",
        ["invited_by_user_id"],
    )
    op.create_index(
        op.f("ix_organization_invitations_organization_id"),
        "organization_invitations",
        ["organization_id"],
    )

    op.create_table(
        "service_accounts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('developer', 'billing_viewer')",
            name=op.f("ck_service_accounts_service_account_role"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name=op.f("ck_service_accounts_service_account_status"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_service_accounts_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_service_accounts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
    )
    op.create_index(
        op.f("ix_service_accounts_created_by_user_id"),
        "service_accounts",
        ["created_by_user_id"],
    )
    op.create_index(
        op.f("ix_service_accounts_organization_id"),
        "service_accounts",
        ["organization_id"],
    )
    op.create_index(op.f("ix_service_accounts_status"), "service_accounts", ["status"])

    op.create_table(
        "audit_events",
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('user', 'service_account', 'api_key', 'system')",
            name=op.f("ck_audit_events_audit_event_actor_type"),
        ),
        sa.CheckConstraint(
            "(actor_type = 'system' AND actor_id IS NULL) OR "
            "(actor_type <> 'system' AND actor_id IS NOT NULL)",
            name=op.f("ck_audit_events_audit_event_actor_identity"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_events_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"])
    op.create_index(op.f("ix_audit_events_actor_type"), "audit_events", ["actor_type"])
    op.create_index(op.f("ix_audit_events_created_at"), "audit_events", ["created_at"])
    op.create_index(
        "ix_audit_events_organization_created",
        "audit_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        op.f("ix_audit_events_organization_id"),
        "audit_events",
        ["organization_id"],
    )
    op.create_index(op.f("ix_audit_events_request_id"), "audit_events", ["request_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("service_accounts")
    op.drop_table("organization_invitations")
    op.drop_table("organization_memberships")
    op.drop_table("users")
    op.drop_table("organizations")
