from app.db.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    ServiceAccount,
)
from app.identity.roles import (
    ORGANIZATION_ROLES,
    PLATFORM_ROLES,
    SERVICE_ACCOUNT_ROLES,
)


def test_customer_roles_are_separate_from_platform_operator_role() -> None:
    assert ORGANIZATION_ROLES == ("owner", "developer", "billing_viewer")
    assert PLATFORM_ROLES == ("platform_operator",)
    assert set(ORGANIZATION_ROLES).isdisjoint(PLATFORM_ROLES)


def test_service_accounts_cannot_be_organization_owners() -> None:
    assert SERVICE_ACCOUNT_ROLES == ("developer", "billing_viewer")
    assert "owner" not in SERVICE_ACCOUNT_ROLES


def test_customer_identity_records_are_scoped_to_an_organization() -> None:
    for model in (OrganizationMembership, OrganizationInvitation, ServiceAccount):
        assert "organization_id" in model.__table__.columns
        assert model.__table__.columns["organization_id"].nullable is False


def test_organization_maps_to_one_legacy_client() -> None:
    client_column = Organization.__table__.columns["client_id"]

    assert client_column.nullable is False
    assert any(
        constraint.name == "uq_organizations_client_id"
        for constraint in Organization.__table__.constraints
    )
