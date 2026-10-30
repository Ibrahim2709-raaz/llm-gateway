from fastapi.testclient import TestClient

from app.main import create_app


def test_portal_api_key_routes_are_in_openapi_contract() -> None:
    with TestClient(create_app()) as client:
        paths = client.get("/openapi.json").json()["paths"]
    collection = "/v1/portal/organizations/{organization_id}/api-keys"
    item = "/v1/portal/organizations/{organization_id}/api-keys/{api_key_id}"
    assert set(paths[collection]) >= {"get", "post"}
    assert "delete" in paths[item]
