import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import Settings
from app.identity.email_delivery import EmailDeliveryError, SmtpPortalEmailDelivery


class FakeSmtpClient:
    def __init__(self) -> None:
        self.starttls_calls = 0
        self.login_credentials: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> "FakeSmtpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self, *, context: object) -> None:
        assert context is not None
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


def smtp_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "portal_action_token_delivery": "smtp",
        "portal_public_base_url": "https://gateway.example.com",
        "smtp_host": "smtp.example.com",
        "smtp_from_email": "gateway@example.com",
        "smtp_from_name": "LLM Gateway",
        "smtp_username": "gateway-user",
        "smtp_password": "smtp-secret",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.anyio
async def test_smtp_delivery_builds_safe_action_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeSmtpClient()
    monkeypatch.setattr(smtplib, "SMTP", lambda *_args, **_kwargs: client)
    delivery = SmtpPortalEmailDelivery(smtp_settings())

    await delivery.send_email_verification(
        recipient="owner@example.com",
        token="verification+/secret",
    )
    await delivery.send_password_reset(
        recipient="owner@example.com",
        token="reset-secret",
    )
    await delivery.send_organization_invitation(
        recipient="member@example.com",
        organization_name="Research & Safety",
        token="invitation-secret",
    )

    assert client.starttls_calls == 3
    assert client.login_credentials == ("gateway-user", "smtp-secret")
    assert len(client.messages) == 3
    verification_text = client.messages[0].get_body(preferencelist=("plain",)).get_content()
    reset_text = client.messages[1].get_body(preferencelist=("plain",)).get_content()
    invitation_html = client.messages[2].get_body(preferencelist=("html",)).get_content()
    assert "#verify=verification%2B%2Fsecret" in verification_text
    assert "#reset=reset-secret" in reset_text
    assert "#invitation=invitation-secret" in invitation_html
    assert "Research &amp; Safety" in invitation_html
    assert "verification+/secret" not in str(client.messages[0]["Subject"])


@pytest.mark.anyio
async def test_smtp_transport_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_connect(*_args: object, **_kwargs: object) -> None:
        raise OSError("connection detail that must stay internal")

    monkeypatch.setattr(smtplib, "SMTP", fail_to_connect)
    delivery = SmtpPortalEmailDelivery(smtp_settings(smtp_username=None, smtp_password=None))

    with pytest.raises(EmailDeliveryError, match="SMTP delivery failed") as error:
        await delivery.send_password_reset(
            recipient="owner@example.com",
            token="reset-secret",
        )
    assert "connection detail" not in str(error.value)
