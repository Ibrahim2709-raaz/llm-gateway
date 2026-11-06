from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

from app.core.config import Settings


class EmailDeliveryError(RuntimeError):
    """An action email could not be handed to the configured SMTP server."""


@dataclass(frozen=True, slots=True)
class PortalActionEmail:
    recipient: str
    subject: str
    text_body: str
    html_body: str


class SmtpPortalEmailDelivery:
    def __init__(self, settings: Settings) -> None:
        if settings.portal_action_token_delivery != "smtp":
            raise ValueError("SMTP portal email delivery is not enabled")
        if settings.smtp_host is None or settings.smtp_from_email is None:
            raise ValueError("SMTP portal email delivery is incomplete")
        self._host = settings.smtp_host.strip()
        self._port = settings.smtp_port
        self._security = settings.smtp_security
        self._username = settings.smtp_username.strip() if settings.smtp_username else None
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self._from_email = settings.smtp_from_email.strip()
        self._from_name = settings.smtp_from_name.strip()
        self._timeout = settings.smtp_timeout_seconds
        self._portal_url = f"{str(settings.portal_public_base_url).rstrip('/')}/portal/"

    async def send_email_verification(self, *, recipient: str, token: str) -> None:
        link = self._action_link("verify", token)
        await self._send(
            PortalActionEmail(
                recipient=recipient,
                subject="Verify your LLM Gateway email",
                text_body=(
                    "Verify your LLM Gateway email by opening this link:\n\n"
                    f"{link}\n\nThis link can be used once."
                ),
                html_body=self._html_message(
                    heading="Verify your email",
                    explanation="Finish creating your LLM Gateway organization.",
                    action="Verify email",
                    link=link,
                ),
            )
        )

    async def send_password_reset(self, *, recipient: str, token: str) -> None:
        link = self._action_link("reset", token)
        await self._send(
            PortalActionEmail(
                recipient=recipient,
                subject="Reset your LLM Gateway password",
                text_body=(
                    "Reset your LLM Gateway password by opening this link:\n\n"
                    f"{link}\n\nIf you did not request this, you can ignore this email."
                ),
                html_body=self._html_message(
                    heading="Reset your password",
                    explanation="Choose a new password for your LLM Gateway account.",
                    action="Reset password",
                    link=link,
                ),
            )
        )

    async def send_organization_invitation(
        self,
        *,
        recipient: str,
        organization_name: str,
        token: str,
    ) -> None:
        link = self._action_link("invitation", token)
        safe_name = html.escape(organization_name)
        await self._send(
            PortalActionEmail(
                recipient=recipient,
                subject=f"Join {organization_name} on LLM Gateway",
                text_body=(
                    f"You were invited to join {organization_name} on LLM Gateway.\n\n"
                    f"Accept the invitation: {link}\n\nThis link can be used once."
                ),
                html_body=self._html_message(
                    heading=f"Join {safe_name}",
                    explanation="Create your account or sign in to join the workspace.",
                    action="Accept invitation",
                    link=link,
                    heading_is_safe=True,
                ),
            )
        )

    def _action_link(self, action: str, token: str) -> str:
        return f"{self._portal_url}#{action}={quote(token, safe='')}"

    @staticmethod
    def _html_message(
        *,
        heading: str,
        explanation: str,
        action: str,
        link: str,
        heading_is_safe: bool = False,
    ) -> str:
        safe_heading = heading if heading_is_safe else html.escape(heading)
        safe_explanation = html.escape(explanation)
        safe_action = html.escape(action)
        safe_link = html.escape(link, quote=True)
        return (
            "<!doctype html><html><body style=\"font-family:Arial,sans-serif;"
            "color:#0b1220\"><h1>"
            f"{safe_heading}</h1><p>{safe_explanation}</p><p><a href=\"{safe_link}\" "
            "style=\"background:#2563eb;color:#fff;padding:12px 18px;"
            f"text-decoration:none;border-radius:6px\">{safe_action}</a></p>"
            f"<p style=\"color:#64748b\">Or open: {safe_link}</p></body></html>"
        )

    async def _send(self, action_email: PortalActionEmail) -> None:
        try:
            await asyncio.to_thread(self._send_sync, action_email)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("SMTP delivery failed") from exc

    def _send_sync(self, action_email: PortalActionEmail) -> None:
        message = EmailMessage()
        message["From"] = formataddr((self._from_name, self._from_email))
        message["To"] = action_email.recipient
        message["Subject"] = action_email.subject
        message.set_content(action_email.text_body)
        message.add_alternative(action_email.html_body, subtype="html")

        context = ssl.create_default_context()
        if self._security == "tls":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout,
                context=context,
            )
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with client:
            client.ehlo()
            if self._security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(message)


def get_portal_email_delivery(settings: Settings) -> SmtpPortalEmailDelivery:
    return SmtpPortalEmailDelivery(settings)
