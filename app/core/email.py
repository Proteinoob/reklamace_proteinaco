"""
Email service for sending complaint and return notifications.
Uses aiosmtplib for async email sending and Jinja2 for HTML templates.
"""
import logging
import re
from typing import Optional

import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_templates_dir = Path(__file__).parent.parent / "templates" / "emails"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html"]),
)


def _sanitize_header(value: str) -> str:
    """Remove CRLF characters to prevent email header injection."""
    return re.sub(r"[\r\n]", "", str(value))


class EmailService:
    """Service for sending complaint/return notification emails."""

    def __init__(self):
        self.smtp_host = settings.SMTP_HOST
        self.smtp_port = settings.SMTP_PORT
        self.smtp_user = settings.SMTP_USER
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.EMAIL_FROM
        self.from_name = settings.EMAIL_FROM_NAME

    async def _send(
        self,
        to_email: str,
        subject: str,
        body_html: str,
        body_text: str,
        attachment: Optional[tuple[str, bytes]] = None,
    ) -> bool:
        """Send an email with HTML and plain-text parts, optional PDF attachment."""
        if not self.smtp_host or not self.smtp_user:
            logger.info("SMTP not configured, skipping email to %s: %s", to_email, subject)
            return False
        try:
            msg = MIMEMultipart("mixed")
            msg["Subject"] = _sanitize_header(subject)
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = _sanitize_header(to_email)

            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body_text, "plain", "utf-8"))
            alt.attach(MIMEText(body_html, "html", "utf-8"))
            msg.attach(alt)

            if attachment:
                filename, data = attachment
                att = MIMEApplication(data, _subtype="pdf")
                att.add_header(
                    "Content-Disposition", "attachment", filename=filename
                )
                msg.attach(att)

            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                use_tls=True,
            )
            logger.info(f"Email sent to {to_email}: {subject}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False

    def _render(self, template_name: str, **kwargs) -> str:
        """Render a Jinja2 email template."""
        template = _jinja_env.get_template(template_name)
        return template.render(**kwargs)

    async def send_return_confirmation(
        self,
        to: str,
        return_code: str,
        items: list[dict],
        label_url: str | None = None,
    ) -> bool:
        """Send confirmation email when a return request is created."""
        html = self._render(
            "return_confirmation.html",
            code=return_code,
            items=items,
            label_url=label_url,
        )
        text = (
            f"Vase zadost o vraceni {return_code} byla prijata. "
            f"Sledujte stav na webu."
        )
        return await self._send(
            to, f"Potvrzeni vratky {return_code}", html, text
        )

    async def send_complaint_confirmation(
        self,
        to: str,
        complaint_code: str,
        items: list[dict],
        label_url: str | None = None,
    ) -> bool:
        """Send confirmation email when a complaint is created."""
        html = self._render(
            "complaint_confirmation.html",
            code=complaint_code,
            items=items,
            label_url=label_url,
        )
        text = (
            f"Vase reklamace {complaint_code} byla prijata. "
            f"Sledujte stav na webu."
        )
        return await self._send(
            to, f"Potvrzeni reklamace {complaint_code}", html, text
        )

    async def send_status_change(
        self,
        to: str,
        case_code: str,
        case_type: str,
        new_status: str,
        status_label: str,
    ) -> bool:
        """Send notification when case status changes."""
        html = self._render(
            "status_change.html",
            code=case_code,
            case_type=case_type,
            new_status=new_status,
            status_label=status_label,
        )
        type_label = "vratky" if case_type == "return" else "reklamace"
        text = (
            f"Stav vasi {type_label} {case_code} se zmenil na: "
            f"{status_label}"
        )
        return await self._send(
            to, f"Zmena stavu {case_code}", html, text
        )

    async def send_rejection(
        self,
        to: str,
        case_code: str,
        case_type: str,
        reason: str,
    ) -> bool:
        """Send notification when a case is rejected."""
        html = self._render(
            "rejection.html",
            code=case_code,
            case_type=case_type,
            reason=reason,
        )
        type_label = "Vratka" if case_type == "return" else "Reklamace"
        text = f"{type_label} {case_code} byla zamitnuta. Duvod: {reason}"
        return await self._send(
            to, f"{type_label} {case_code} — zamitnuto", html, text
        )

    async def send_request_info(
        self,
        to: str,
        complaint_code: str,
        message: str,
        supplement_url: str,
    ) -> bool:
        """Send request for additional information from customer."""
        html = self._render(
            "request_info.html",
            code=complaint_code,
            message=message,
            supplement_url=supplement_url,
        )
        text = (
            f"K reklamaci {complaint_code} potrebujeme doplnit informace: "
            f"{message}"
        )
        return await self._send(
            to, f"Doplneni k reklamaci {complaint_code}", html, text
        )

    async def send_resolution(
        self,
        to: str,
        case_code: str,
        case_type: str,
        resolution_type: str,
        details: str,
    ) -> bool:
        """Send notification when a case is resolved."""
        html = self._render(
            "resolution.html",
            code=case_code,
            case_type=case_type,
            resolution_type=resolution_type,
            details=details,
        )
        type_label = "Vratka" if case_type == "return" else "Reklamace"
        text = f"{type_label} {case_code} byla vyresena. {details}"
        return await self._send(
            to, f"{type_label} {case_code} — vyreseno", html, text
        )
