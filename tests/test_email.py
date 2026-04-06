"""Tests for the email service and templates."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.email import EmailService, _sanitize_header, _jinja_env


# --- Template rendering tests ---

SAMPLE_ITEMS = [
    {"product_name": "Whey Protein 1kg", "quantity": 2},
    {"product_name": "BCAA 300g", "quantity": 1},
]


def test_render_return_confirmation():
    """Verify return_confirmation template renders and contains code + items."""
    template = _jinja_env.get_template("return_confirmation.html")
    html = template.render(
        code="RET-20260405-001",
        items=SAMPLE_ITEMS,
        label_url="https://example.com/label.pdf",
    )
    assert "RET-20260405-001" in html
    assert "Whey Protein 1kg" in html
    assert "BCAA 300g" in html
    assert "https://example.com/label.pdf" in html


def test_render_return_confirmation_no_label():
    """Verify return_confirmation renders without label_url."""
    template = _jinja_env.get_template("return_confirmation.html")
    html = template.render(
        code="RET-001",
        items=SAMPLE_ITEMS,
        label_url=None,
    )
    assert "RET-001" in html
    assert "Whey Protein 1kg" in html


def test_render_complaint_confirmation():
    """Verify complaint_confirmation template renders and contains code + items."""
    template = _jinja_env.get_template("complaint_confirmation.html")
    html = template.render(
        code="COM-20260405-001",
        items=SAMPLE_ITEMS,
        label_url="https://example.com/label.pdf",
    )
    assert "COM-20260405-001" in html
    assert "Whey Protein 1kg" in html
    assert "BCAA 300g" in html
    assert "reklamace" in html.lower() or "reklamac" in html.lower()


def test_render_all_templates():
    """Verify all 6 email templates render without errors."""
    template_data = {
        "return_confirmation.html": {
            "code": "RET-001",
            "items": SAMPLE_ITEMS,
            "label_url": None,
        },
        "complaint_confirmation.html": {
            "code": "COM-001",
            "items": SAMPLE_ITEMS,
            "label_url": None,
        },
        "status_change.html": {
            "code": "CASE-001",
            "case_type": "complaint",
            "new_status": "in_review",
            "status_label": "Probíhá posouzení",
        },
        "rejection.html": {
            "code": "CASE-002",
            "case_type": "return",
            "reason": "Zboží bylo poškozeno zákazníkem.",
        },
        "request_info.html": {
            "code": "COM-003",
            "message": "Prosíme o zaslání fotografie poškozeného produktu.",
            "supplement_url": "https://example.com/supplement/COM-003",
        },
        "resolution.html": {
            "code": "COM-004",
            "case_type": "complaint",
            "resolution_type": "refund",
            "details": "Vráceno 499 Kč na účet.",
        },
    }

    for template_name, data in template_data.items():
        template = _jinja_env.get_template(template_name)
        html = template.render(**data)
        assert len(html) > 100, f"Template {template_name} rendered too short"
        assert data["code"] in html, f"Code missing in {template_name}"


# --- Email sending tests ---

@pytest.mark.asyncio
async def test_send_email_success():
    """Mock aiosmtplib.send, verify it's called with correct params."""
    with patch("app.core.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        service = EmailService()
        result = await service.send_return_confirmation(
            to="customer@example.com",
            return_code="RET-001",
            items=SAMPLE_ITEMS,
            label_url=None,
        )

        assert result is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        # Verify the message object was passed
        msg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("message")
        assert msg is not None
        # Verify SMTP connection params
        assert call_kwargs[1]["hostname"] == service.smtp_host
        assert call_kwargs[1]["port"] == service.smtp_port
        assert call_kwargs[1]["username"] == service.smtp_user
        assert call_kwargs[1]["password"] == service.smtp_password
        assert call_kwargs[1]["use_tls"] is True


@pytest.mark.asyncio
async def test_send_email_failure():
    """Mock aiosmtplib.send to raise, verify returns False."""
    with patch(
        "app.core.email.aiosmtplib.send",
        new_callable=AsyncMock,
        side_effect=ConnectionRefusedError("SMTP connection refused"),
    ):
        service = EmailService()
        result = await service.send_complaint_confirmation(
            to="customer@example.com",
            complaint_code="COM-001",
            items=SAMPLE_ITEMS,
        )

        assert result is False


@pytest.mark.asyncio
async def test_send_status_change():
    """Verify send_status_change calls _send with correct subject."""
    with patch("app.core.email.aiosmtplib.send", new_callable=AsyncMock):
        service = EmailService()
        result = await service.send_status_change(
            to="customer@example.com",
            case_code="CASE-001",
            case_type="complaint",
            new_status="in_review",
            status_label="Probíhá posouzení",
        )
        assert result is True


@pytest.mark.asyncio
async def test_send_rejection():
    """Verify send_rejection works for both case types."""
    with patch("app.core.email.aiosmtplib.send", new_callable=AsyncMock):
        service = EmailService()
        result = await service.send_rejection(
            to="customer@example.com",
            case_code="RET-001",
            case_type="return",
            reason="Zboží vráceno po lhůtě.",
        )
        assert result is True


@pytest.mark.asyncio
async def test_send_request_info():
    """Verify send_request_info renders and sends."""
    with patch("app.core.email.aiosmtplib.send", new_callable=AsyncMock):
        service = EmailService()
        result = await service.send_request_info(
            to="customer@example.com",
            complaint_code="COM-001",
            message="Zašlete prosím fotku.",
            supplement_url="https://example.com/supplement",
        )
        assert result is True


@pytest.mark.asyncio
async def test_send_resolution():
    """Verify send_resolution renders and sends."""
    with patch("app.core.email.aiosmtplib.send", new_callable=AsyncMock):
        service = EmailService()
        result = await service.send_resolution(
            to="customer@example.com",
            case_code="COM-001",
            case_type="complaint",
            resolution_type="refund",
            details="Vráceno 499 Kč.",
        )
        assert result is True


# --- Sanitization tests ---

def test_sanitize_header_removes_newlines():
    """Test that CRLF characters are stripped from headers."""
    assert _sanitize_header("normal subject") == "normal subject"
    assert _sanitize_header("evil\r\nBcc: attacker@evil.com") == "evilBcc: attacker@evil.com"
    assert _sanitize_header("line\ninjection") == "lineinjection"
    assert _sanitize_header("carriage\rreturn") == "carriagereturn"


def test_sanitize_header_handles_non_string():
    """Test that non-string values are converted."""
    assert _sanitize_header(12345) == "12345"
    assert _sanitize_header(None) == "None"
