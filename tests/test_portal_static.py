from fastapi.testclient import TestClient

from app.main import create_app


def test_portal_shell_and_assets_are_served_by_gateway() -> None:
    with TestClient(create_app()) as client:
        page = client.get("/portal/")
        styles = client.get("/portal/styles.css")
        script = client.get("/portal/app.js")

    assert page.status_code == 200
    assert "One governed path to every model" in page.text
    assert 'id="routing-decisions"' in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert styles.status_code == 200
    assert "--canvas: #f7f2e8" in styles.text
    assert script.status_code == 200
    assert 'sessionStorage.getItem("llm_gateway_csrf")' in script.text


def test_portal_has_no_remote_runtime_dependencies() -> None:
    with TestClient(create_app()) as client:
        page = client.get("/portal/").text
    assert "https://" not in page
    assert "http://" not in page
