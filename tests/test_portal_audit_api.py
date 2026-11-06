from fastapi.testclient import TestClient

from app.main import create_app


def test_portal_audit_feed_is_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/portal/organizations/{organization_id}/audit-events" in paths
