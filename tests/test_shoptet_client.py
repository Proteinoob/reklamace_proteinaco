"""Tests for Shoptet API client service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.shoptet_client import ShoptetClient


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
    content: bytes = b"",
) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.content = content
    resp.request = MagicMock(spec=httpx.Request)

    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=resp.request,
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None

    return resp


def _build_client() -> ShoptetClient:
    """Create a ShoptetClient with dummy credentials."""
    return ShoptetClient(api_token="test-token", api_base="https://api.test.com")


@pytest.mark.asyncio
async def test_get_order_success():
    """GET order returns parsed data from the 'data' envelope."""
    order_data = {
        "code": "OBJ-001",
        "items": [{"name": "Protein bar", "amount": 2}],
    }
    mock_resp = _mock_response(json_data={"data": order_data})

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.return_value = mock_resp

    client = _build_client()
    client._client = mock_http

    result = await client.get_order("OBJ-001")

    assert result == order_data
    mock_http.request.assert_called_once_with(
        "GET",
        "/api/orders/OBJ-001",
        params={"include": "items"},
    )
    await client.close()


@pytest.mark.asyncio
async def test_get_order_not_found():
    """GET order with 404 raises HTTPStatusError."""
    mock_resp = _mock_response(status_code=404)

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.return_value = mock_resp

    client = _build_client()
    client._client = mock_http

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_order("NONEXISTENT")

    await client.close()


@pytest.mark.asyncio
async def test_create_credit_note_success():
    """POST to create credit note returns parsed data."""
    cn_data = {"code": "CN-001", "invoiceCode": "INV-001"}
    mock_resp = _mock_response(json_data={"data": cn_data})

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.return_value = mock_resp

    client = _build_client()
    client._client = mock_http

    result = await client.create_credit_note("INV-001")

    assert result == cn_data
    mock_http.request.assert_called_once_with(
        "POST",
        "/api/invoices/INV-001/credit-note",
        json=None,
    )
    await client.close()


@pytest.mark.asyncio
async def test_retry_on_rate_limit():
    """Client retries on 429 and succeeds on the next attempt."""
    rate_limited = _mock_response(status_code=429)
    # 429 should NOT call raise_for_status — override it
    rate_limited.raise_for_status.side_effect = None

    success_data = {"data": {"code": "OBJ-002"}}
    ok_resp = _mock_response(json_data=success_data)

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.side_effect = [rate_limited, ok_resp]

    client = _build_client()
    client._client = mock_http

    # Patch asyncio.sleep so we don't actually wait
    with patch("app.services.shoptet_client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.get_order("OBJ-002")

    assert result == {"code": "OBJ-002"}
    assert mock_http.request.call_count == 2
    await client.close()


@pytest.mark.asyncio
async def test_retry_exhausted():
    """Client raises after MAX_RETRIES consecutive 429 responses."""
    rate_limited = _mock_response(status_code=429)
    rate_limited.raise_for_status.side_effect = None

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.return_value = rate_limited

    client = _build_client()
    client._client = mock_http

    with patch("app.services.shoptet_client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.HTTPStatusError, match="Rate limited after max retries"):
            await client.get_order("OBJ-003")

    assert mock_http.request.call_count == ShoptetClient.MAX_RETRIES
    await client.close()


@pytest.mark.asyncio
async def test_get_credit_note_pdf():
    """get_credit_note_pdf returns raw PDF bytes."""
    pdf_bytes = b"%PDF-1.4 fake content"
    mock_resp = _mock_response(content=pdf_bytes)

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = mock_resp

    client = _build_client()
    client._client = mock_http

    result = await client.get_credit_note_pdf("CN-001")

    assert result == pdf_bytes
    mock_http.get.assert_called_once_with("/api/credit-notes/CN-001/pdf")
    await client.close()


@pytest.mark.asyncio
async def test_context_manager():
    """Verify async context manager opens and closes client."""
    async with ShoptetClient(api_token="t", api_base="https://test.com") as client:
        assert client._client is None  # lazy init, not yet created

    # After exiting, _client should be None (closed or never opened)
    assert client._client is None


@pytest.mark.asyncio
async def test_response_without_data_envelope():
    """When response JSON has no 'data' key, return the full body."""
    raw_body = {"status": "ok", "message": "done"}
    mock_resp = _mock_response(json_data=raw_body)

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request.return_value = mock_resp

    client = _build_client()
    client._client = mock_http

    result = await client.get("/api/some-endpoint")

    assert result == raw_body
    await client.close()
