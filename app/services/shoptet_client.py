import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ShoptetClient:
    """Async HTTP client for the Shoptet Private API."""

    MAX_RETRIES = 3
    RETRY_BACKOFF = 2

    def __init__(self, api_token: str | None = None, api_base: str | None = None):
        self.api_token = api_token or settings.SHOPTET_TOKEN_CZ
        self.api_base = api_base or settings.SHOPTET_API_BASE
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-initialize the underlying httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_base,
                headers={"Shoptet-Private-Api-Token": self.api_token},
                timeout=30.0,
            )
        return self._client

    async def close(self):
        """Close the underlying HTTP client if open."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Execute an HTTP request with retry logic for rate limiting (429)."""
        client = await self._get_client()
        resp: httpx.Response | None = None

        for attempt in range(self.MAX_RETRIES):
            resp = await client.request(method, path, **kwargs)

            if resp.status_code == 429:
                wait = self.RETRY_BACKOFF * (attempt + 1)
                logger.warning(f"Shoptet rate limited, retry in {wait}s")
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            body = resp.json()
            if "data" in body:
                return body["data"]
            return body

        # Exhausted all retries — raise with last response
        raise httpx.HTTPStatusError(
            "Rate limited after max retries",
            request=resp.request,  # type: ignore[union-attr]
            response=resp,  # type: ignore[arg-type]
        )

    async def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """Send a GET request."""
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict | None = None) -> dict[str, Any]:
        """Send a POST request."""
        return await self._request("POST", path, json=json)

    # --- Domain methods ---

    async def get_order(self, order_code: str) -> dict[str, Any]:
        """Fetch order details including items from Shoptet."""
        return await self.get(f"/api/orders/{order_code}")

    async def create_credit_note(self, invoice_code: str) -> dict[str, Any]:
        """Create a credit note (dobropis) from an existing invoice."""
        return await self.post(f"/api/invoices/{invoice_code}/credit-note")

    async def get_credit_note_pdf(self, credit_note_code: str) -> bytes:
        """Download credit note as a PDF binary."""
        client = await self._get_client()
        resp = await client.get(f"/api/credit-notes/{credit_note_code}/pdf")
        resp.raise_for_status()
        return resp.content
