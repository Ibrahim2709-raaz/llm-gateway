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
    assert 'id="invitation-form"' in page.text
    assert 'id="resend-verification-form"' in page.text
    assert 'id="playground-form"' in page.text
    assert 'data-view="playground"' in page.text
    assert 'data-view="service-accounts"' in page.text
    assert 'data-view="admin-models"' in page.text
    assert 'id="model-catalog-table"' in page.text
    assert 'id="pricing-proposals-table"' in page.text
    assert 'id="new-pricing-proposal-button"' in page.text
    assert 'id="budget-alert"' in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert styles.status_code == 200
    assert "--canvas: #f7f2e8" in styles.text
    assert script.status_code == 200
    assert 'sessionStorage.getItem("llm_gateway_csrf")' in script.text
    assert 'window.location.hash.slice(1)' in script.text
    assert '/v1/portal/invitations/accept-new' in script.text
    assert '/v1/portal/auth/verify-email/resend' in script.text
    assert 'action.get("verify")' in script.text
    assert 'action.get("reset")' in script.text
    assert "/playground/preview" in script.text
    assert "/playground/completions" in script.text
    assert "/service-accounts" in script.text
    assert "/v1/portal/admin/models" in script.text
    assert "/v1/portal/admin/pricing-proposals" in script.text
    assert 'platform_role === "platform_operator"' in script.text
    assert "/rotate" in script.text
    assert 'name="prompt"' not in script.text


def test_portal_has_no_remote_runtime_dependencies() -> None:
    with TestClient(create_app()) as client:
        page = client.get("/portal/").text
    assert "https://" not in page
    assert "http://" not in page
