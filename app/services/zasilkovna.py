import logging
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ZasilkovnaError(Exception):
    """Custom error for Zasilkovna API failures."""
    pass


class ZasilkovnaClient:
    API_URL = "https://www.zasilkovna.cz/api/rest"
    LABEL_URL = "https://www.zasilkovna.cz/api/packetLabelPdf"

    def __init__(self, api_key: str | None = None, sender_id: str | None = None):
        self.api_key = api_key or settings.ZASILKOVNA_API_KEY
        self.sender_id = sender_id or settings.ZASILKOVNA_SENDER_ID
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # Warehouse address for return shipments
    WAREHOUSE = {
        "name": "Strongmed",
        "surname": "s.r.o.",
        "street": "nám. Svobody 268",
        "city": "Kojetín",
        "zip": "75201",
        "country": "cz",
    }

    # Zásilkovna carrier ID for CZ address delivery
    CARRIER_ID_CZ_ADDRESS = 106

    def _build_return_packet_xml(
        self,
        number: str,
        name: str,
        surname: str,
        email: str,
        phone: str,
        value: float,
        weight: float,
        eshop: str,
    ) -> str:
        """Build XML for createPacket — return shipment to warehouse address."""
        wh = self.WAREHOUSE
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<createPacket>"
            f"<apiPassword>{self.api_key}</apiPassword>"
            "<packetAttributes>"
            f"<number>{number}</number>"
            f"<name>{wh['name']}</name>"
            f"<surname>{wh['surname']}</surname>"
            f"<email>{email}</email>"
            f"<phone>{phone}</phone>"
            f"<addressId>{self.CARRIER_ID_CZ_ADDRESS}</addressId>"
            f"<currency>CZK</currency>"
            f"<value>{value}</value>"
            f"<weight>{weight}</weight>"
            f"<eshop>{eshop}</eshop>"
            f"<street>{wh['street']}</street>"
            f"<city>{wh['city']}</city>"
            f"<zip>{wh['zip']}</zip>"
            f"<senderName>{name}</senderName>"
            f"<senderSurname>{surname}</senderSurname>"
            f"<senderEmail>{email}</senderEmail>"
            f"<senderPhone>{phone}</senderPhone>"
            "</packetAttributes>"
            "</createPacket>"
        )
        return xml

    async def create_return_packet(
        self,
        case_code: str,
        customer_name: str,
        customer_surname: str,
        customer_email: str,
        customer_phone: str,
        value: float,
        weight: float = 1.0,
    ) -> dict[str, Any]:
        """Create a return shipment packet in Zásilkovna.

        The packet is addressed to the warehouse. The customer is the sender.
        Returns dict with 'packet_id' and 'barcode'.
        """
        xml_body = self._build_return_packet_xml(
            number=case_code,
            name=customer_name,
            surname=customer_surname,
            email=customer_email,
            phone=customer_phone or "",
            value=value,
            weight=weight,
            eshop="proteinaco.cz",
        )

        client = await self._get_client()
        resp = await client.post(
            self.API_URL,
            content=xml_body,
            headers={"Content-Type": "application/xml"},
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        status = root.find(".//status")

        if status is not None and status.text == "ok":
            result = root.find(".//result")
            packet_id = (
                result.find("id").text
                if result is not None and result.find("id") is not None
                else None
            )
            barcode = (
                result.find("barcode").text
                if result is not None and result.find("barcode") is not None
                else None
            )
            return {"packet_id": packet_id, "barcode": barcode}

        # Error handling — try multiple XML structures
        error_msg = "Unknown error"
        # Try <string> element (common in Zásilkovna responses)
        string_el = root.find(".//string")
        if string_el is not None and string_el.text:
            error_msg = string_el.text
        # Try <fault> with <faultString>
        fault = root.find(".//fault")
        if fault is not None:
            fault_string = fault.find("faultString")
            if fault_string is not None and fault_string.text:
                error_msg = fault_string.text
            elif fault.text and fault.text.strip():
                error_msg = fault.text.strip()
        # Try detail for specific attribute errors
        detail = root.find(".//detail")
        if detail is not None:
            detail_faults = detail.findall(".//fault")
            detail_msgs = [f.text for f in detail_faults if f.text]
            if detail_msgs:
                error_msg += " | " + " | ".join(detail_msgs)

        logger.error("Zasilkovna createPacket failed: %s | Full XML: %s", error_msg, resp.text[:500])
        raise ZasilkovnaError(error_msg)

    async def get_label_pdf(self, packet_id: str) -> bytes:
        """Download shipping label as PDF for a given packet.

        Uses REST XML API — returns base64-encoded PDF in <result> element.
        """
        import base64

        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            "<packetLabelPdf>"
            f"<apiPassword>{self.api_key}</apiPassword>"
            f"<packetId>{packet_id}</packetId>"
            "<format>A7 on A4</format>"
            "<offset>0</offset>"
            "</packetLabelPdf>"
        )

        client = await self._get_client()
        resp = await client.post(
            self.API_URL,
            content=xml,
            headers={"Content-Type": "application/xml"},
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        status = root.find(".//status")

        if status is not None and status.text == "ok":
            result = root.find(".//result")
            if result is not None and result.text:
                return base64.b64decode(result.text)

        fault = root.find(".//string")
        error_msg = fault.text if fault is not None else "Label generation failed"
        raise ZasilkovnaError(error_msg)
