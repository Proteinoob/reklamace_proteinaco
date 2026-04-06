from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.services.zasilkovna import ZasilkovnaClient, ZasilkovnaError

SUCCESS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<response>
    <status>ok</status>
    <result>
        <id>1234567890</id>
        <barcode>Z1234567890</barcode>
    </result>
</response>"""

ERROR_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<response>
    <status>fault</status>
    <fault>
        <faultString>Invalid API key</faultString>
    </fault>
</response>"""

LABEL_ERROR_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<response>
    <status>fault</status>
    <fault>
        <faultString>Packet not found</faultString>
    </fault>
</response>"""

PDF_BYTES = b"%PDF-1.4 fake-pdf-content-for-testing"


def _make_response(
    status_code: int = 200,
    text: str = "",
    content: bytes = b"",
    content_type: str = "application/xml",
) -> httpx.Response:
    """Build a mock httpx.Response with a dummy request attached."""
    headers = {"content-type": content_type}
    dummy_request = httpx.Request("GET", "https://example.com")
    if content:
        resp = httpx.Response(
            status_code=status_code, content=content, headers=headers
        )
    else:
        resp = httpx.Response(
            status_code=status_code, text=text, headers=headers
        )
    resp._request = dummy_request
    return resp


@pytest.fixture
def client():
    return ZasilkovnaClient(api_key="test-api-key", sender_id="12345")


# ---- create_return_packet ----


@pytest.mark.asyncio
async def test_create_return_packet_success(client):
    mock_resp = _make_response(text=SUCCESS_XML)
    with patch.object(client, "_get_client") as mock_get:
        http_client = AsyncMock()
        http_client.post.return_value = mock_resp
        mock_get.return_value = http_client

        result = await client.create_return_packet(
            case_code="RK-2026-001",
            customer_name="Jan",
            customer_surname="Novak",
            customer_email="jan@example.com",
            customer_phone="+420123456789",
            value=599.0,
            weight=0.5,
        )

    assert result["packet_id"] == "1234567890"
    assert result["barcode"] == "Z1234567890"
    http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_create_return_packet_error(client):
    mock_resp = _make_response(text=ERROR_XML)
    with patch.object(client, "_get_client") as mock_get:
        http_client = AsyncMock()
        http_client.post.return_value = mock_resp
        mock_get.return_value = http_client

        with pytest.raises(ZasilkovnaError, match="Invalid API key"):
            await client.create_return_packet(
                case_code="RK-2026-002",
                customer_name="Jana",
                customer_surname="Novakova",
                customer_email="jana@example.com",
                customer_phone="+420987654321",
                value=299.0,
            )


# ---- get_label_pdf ----


@pytest.mark.asyncio
async def test_get_label_pdf_success(client):
    mock_resp = _make_response(
        content=PDF_BYTES, content_type="application/pdf"
    )
    with patch.object(client, "_get_client") as mock_get:
        http_client = AsyncMock()
        http_client.get.return_value = mock_resp
        mock_get.return_value = http_client

        pdf = await client.get_label_pdf("1234567890")

    assert pdf == PDF_BYTES
    http_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_get_label_pdf_error(client):
    mock_resp = _make_response(
        text=LABEL_ERROR_XML, content_type="application/xml"
    )
    with patch.object(client, "_get_client") as mock_get:
        http_client = AsyncMock()
        http_client.get.return_value = mock_resp
        mock_get.return_value = http_client

        with pytest.raises(ZasilkovnaError, match="Packet not found"):
            await client.get_label_pdf("9999999999")


# ---- XML building ----


def test_xml_building(client):
    xml_str = client._build_create_packet_xml(
        number="RK-2026-003",
        name="Petr",
        surname="Svoboda",
        email="petr@example.com",
        phone="+420111222333",
        address_id=12345,
        value=499.0,
        weight=1.5,
        eshop="proteinaco.cz",
    )

    root = ET.fromstring(xml_str)

    assert root.tag == "createPacket"
    assert root.find("apiPassword").text == "test-api-key"

    attrs = root.find("packetAttributes")
    assert attrs is not None
    assert attrs.find("number").text == "RK-2026-003"
    assert attrs.find("name").text == "Petr"
    assert attrs.find("surname").text == "Svoboda"
    assert attrs.find("email").text == "petr@example.com"
    assert attrs.find("phone").text == "+420111222333"
    assert attrs.find("addressId").text == "12345"
    assert attrs.find("value").text == "499.0"
    assert attrs.find("weight").text == "1.5"
    assert attrs.find("eshop").text == "proteinaco.cz"
