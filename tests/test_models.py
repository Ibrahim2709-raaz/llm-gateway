from app.db.base import Base
from app.db.models import ApiKey, BudgetLimit, Client, ProviderConfig, UsageRecord


def test_initial_database_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        ApiKey.__tablename__,
        BudgetLimit.__tablename__,
        Client.__tablename__,
        ProviderConfig.__tablename__,
        UsageRecord.__tablename__,
    }


def test_sensitive_api_key_material_is_not_stored() -> None:
    columns = set(ApiKey.__table__.columns.keys())

    assert "key_hash" in columns
    assert "key_prefix" in columns
    assert "key" not in columns
