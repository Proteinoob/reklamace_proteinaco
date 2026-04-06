import io
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.schemas.common import (
    OrderLookupResponse,
    OrderProductItem,
    StatusHistoryEntry,
)
from app.schemas.return_schemas import (
    ReturnCreateResponse,
    ReturnTrackingResponse,
    ReturnItemDetail,
)
from app.schemas.complaint_schemas import (
    ComplaintCreateResponse,
    ComplaintTrackingResponse,
    ComplaintItemDetail,
    ComplaintPhotoDetail,
)
from app.services.photo_service import PhotoValidationError


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Clear rate limiter state before every test."""
    from app.api.customer import _rate_limits
    _rate_limits.clear()
    yield
    _rate_limits.clear()


# --------------- Lookup order ---------------


@patch("app.api.customer.return_service.lookup_order", new_callable=AsyncMock)
def test_lookup_order_success(mock_lookup, client):
    mock_lookup.return_value = OrderLookupResponse(
        order_code="OBJ-2026-001",
        customer_name="Jan Novak",
        customer_email="jan@example.com",
        order_date="2026-01-15",
        items=[
            OrderProductItem(
                product_code="WHEY-001",
                product_name="Whey Protein 1kg",
                quantity=2,
                unit_price=599.0,
            )
        ],
    )

    resp = client.post(
        "/api/v1/customer/lookup-order",
        json={"order_code": "OBJ-2026-001", "email": "jan@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["order_code"] == "OBJ-2026-001"
    assert data["customer_name"] == "Jan Novak"
    assert len(data["items"]) == 1
    assert data["items"][0]["product_code"] == "WHEY-001"
    mock_lookup.assert_called_once()


@patch("app.api.customer.return_service.lookup_order", new_callable=AsyncMock)
def test_lookup_order_not_found(mock_lookup, client):
    mock_lookup.side_effect = ValueError("Objednavka nebyla nalezena")

    resp = client.post(
        "/api/v1/customer/lookup-order",
        json={"order_code": "OBJ-MISSING", "email": "test@example.com"},
    )
    assert resp.status_code == 404
    assert "Objednavka nebyla nalezena" in resp.json()["detail"]


def test_lookup_order_invalid_body(client):
    # Missing 'email' field
    resp = client.post(
        "/api/v1/customer/lookup-order",
        json={"order_code": "OBJ-001"},
    )
    assert resp.status_code == 422


# --------------- Create return ---------------


@patch("app.api.customer.return_service.create_return", new_callable=AsyncMock)
def test_create_return_success(mock_create, client):
    mock_create.return_value = ReturnCreateResponse(
        code="RV-2026-0001",
        instructions="Vase zadost o vraceni RV-2026-0001 byla prijata.",
        label_url="https://www.zasilkovna.cz/api/packetLabelPdf?packetId=123",
    )

    resp = client.post(
        "/api/v1/customer/returns",
        json={
            "order_code": "OBJ-2026-001",
            "email": "jan@example.com",
            "name": "Jan Novak",
            "phone": "+420777888999",
            "bank_account": "1234567890/0100",
            "items": [
                {
                    "product_code": "WHEY-001",
                    "quantity": 1,
                    "reason": "not_satisfied",
                    "comment": "Not what I expected",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "RV-2026-0001"
    assert data["label_url"] is not None
    mock_create.assert_called_once()


@patch("app.api.customer.return_service.create_return", new_callable=AsyncMock)
def test_create_return_validation_error(mock_create, client):
    mock_create.side_effect = ValueError("Email neodpovida objednavce")

    resp = client.post(
        "/api/v1/customer/returns",
        json={
            "order_code": "OBJ-2026-001",
            "email": "wrong@example.com",
            "name": "Jan Novak",
            "items": [
                {
                    "product_code": "WHEY-001",
                    "quantity": 1,
                    "reason": "not_satisfied",
                }
            ],
        },
    )
    assert resp.status_code == 400
    assert "Email neodpovida objednavce" in resp.json()["detail"]


# --------------- Create complaint ---------------


@patch("app.api.customer.complaint_service.create_complaint", new_callable=AsyncMock)
def test_create_complaint_success(mock_create, client):
    mock_create.return_value = ComplaintCreateResponse(
        code="RE-2026-0001",
        instructions="Vasi reklamaci jsme prijali.",
        label_url=None,
    )

    resp = client.post(
        "/api/v1/customer/complaints",
        json={
            "order_code": "OBJ-2026-001",
            "email": "jan@example.com",
            "name": "Jan Novak",
            "items": [
                {
                    "product_code": "WHEY-001",
                    "quantity": 1,
                    "problem_description": "Spatna chut",
                    "preferred_resolution": "refund",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "RE-2026-0001"
    mock_create.assert_called_once()


# --------------- Upload photos ---------------


@patch("app.api.customer.complaint_service.upload_photos", new_callable=AsyncMock)
def test_upload_photos_success(mock_upload, client):
    mock_photo = MagicMock()
    mock_photo.id = 1
    mock_photo.original_filename = "photo.jpg"
    mock_upload.return_value = [mock_photo]

    files = [("files", ("photo.jpg", io.BytesIO(b"fake-image-data"), "image/jpeg"))]
    resp = client.post(
        "/api/v1/customer/complaints/RE-2026-0001/photos",
        files=files,
        data={"email": "test@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["uploaded"] == 1
    mock_upload.assert_called_once()


@patch("app.api.customer.complaint_service.upload_photos", new_callable=AsyncMock)
def test_upload_photos_validation_error(mock_upload, client):
    mock_upload.side_effect = PhotoValidationError("Maximum 5 photos per complaint")

    files = [("files", ("photo.jpg", io.BytesIO(b"fake-image-data"), "image/jpeg"))]
    resp = client.post(
        "/api/v1/customer/complaints/RE-2026-0001/photos",
        files=files,
        data={"email": "test@example.com"},
    )
    assert resp.status_code == 400
    assert "Maximum 5 photos" in resp.json()["detail"]


# --------------- Track return ---------------


@patch("app.api.customer.return_service.get_return_by_code")
def test_track_return_success(mock_get, client):
    now = datetime.now(timezone.utc)
    mock_get.return_value = ReturnTrackingResponse(
        code="RV-2026-0001",
        status="waiting_for_delivery",
        status_label="Ceka na doruceni",
        items=[
            ReturnItemDetail(
                id=1,
                product_code="WHEY-001",
                product_name="Whey Protein 1kg",
                quantity=1,
                unit_price=599.0,
                reason="not_satisfied",
                refund_amount=599.0,
            )
        ],
        status_history=[
            StatusHistoryEntry(
                new_status="new",
                changed_by="customer",
                note="Zadost vytvorena",
                created_at=now,
            )
        ],
        created_at=now,
    )

    resp = client.get(
        "/api/v1/customer/returns/RV-2026-0001",
        params={"email": "jan@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "RV-2026-0001"
    assert data["status"] == "waiting_for_delivery"
    mock_get.assert_called_once()


@patch("app.api.customer.return_service.get_return_by_code")
def test_track_return_wrong_email(mock_get, client):
    mock_get.side_effect = ValueError("Email neodpovida zaznamu vraceni")

    resp = client.get(
        "/api/v1/customer/returns/RV-2026-0001",
        params={"email": "wrong@example.com"},
    )
    assert resp.status_code == 404
    assert "Email neodpovida" in resp.json()["detail"]


# --------------- Track complaint ---------------


@patch("app.api.customer.complaint_service.get_complaint_by_code")
def test_track_complaint_success(mock_get, client):
    now = datetime.now(timezone.utc)
    mock_get.return_value = ComplaintTrackingResponse(
        code="RE-2026-0001",
        status="waiting_for_assessment",
        status_label="Ceka na posouzeni",
        items=[
            ComplaintItemDetail(
                id=1,
                product_code="WHEY-001",
                product_name="Whey Protein 1kg",
                quantity=1,
                unit_price=599.0,
                problem_description="Spatna chut",
            )
        ],
        photos=[
            ComplaintPhotoDetail(
                id=1,
                original_filename="photo.jpg",
                uploaded_at=now,
            )
        ],
        status_history=[
            StatusHistoryEntry(
                new_status="new",
                changed_by="customer",
                note="Reklamace vytvorena",
                created_at=now,
            )
        ],
        created_at=now,
    )

    resp = client.get(
        "/api/v1/customer/complaints/RE-2026-0001",
        params={"email": "jan@example.com"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "RE-2026-0001"
    assert data["status"] == "waiting_for_assessment"
    assert len(data["photos"]) == 1
    mock_get.assert_called_once()


# --------------- Supplement complaint ---------------


@patch("app.api.customer.complaint_service.supplement_complaint", new_callable=AsyncMock)
def test_supplement_complaint_success(mock_supplement, client):
    mock_complaint = MagicMock()
    mock_complaint.code = "RE-2026-0001"
    mock_complaint.status = "waiting_for_assessment"
    mock_supplement.return_value = mock_complaint

    resp = client.post(
        "/api/v1/customer/complaints/RE-2026-0001/supplement",
        json={
            "email": "jan@example.com",
            "message": "Here is additional information about the issue.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "RE-2026-0001"
    assert data["status"] == "waiting_for_assessment"
    mock_supplement.assert_called_once()


@patch("app.api.customer.complaint_service.supplement_complaint", new_callable=AsyncMock)
def test_supplement_complaint_wrong_status(mock_supplement, client):
    mock_supplement.side_effect = ValueError(
        "Cannot supplement complaint in status approved."
    )

    resp = client.post(
        "/api/v1/customer/complaints/RE-2026-0001/supplement",
        json={
            "email": "jan@example.com",
            "message": "More info",
        },
    )
    assert resp.status_code == 400
    assert "Cannot supplement" in resp.json()["detail"]


# --------------- Rate limiting ---------------


def test_rate_limiting(client):
    """Verify that rate limiting triggers 429 after exceeding the limit."""
    # lookup-order has limit=10 per minute
    # We need to mock the service to avoid real calls
    with patch(
        "app.api.customer.return_service.lookup_order",
        new_callable=AsyncMock,
    ) as mock_lookup:
        mock_lookup.return_value = OrderLookupResponse(
            order_code="OBJ-001",
            customer_name="Test",
            customer_email="test@example.com",
            items=[],
        )

        # Make 10 successful requests
        for i in range(10):
            resp = client.post(
                "/api/v1/customer/lookup-order",
                json={"order_code": "OBJ-001", "email": "test@example.com"},
            )
            assert resp.status_code == 200, f"Request {i+1} failed with {resp.status_code}"

        # 11th request should be rate limited
        resp = client.post(
            "/api/v1/customer/lookup-order",
            json={"order_code": "OBJ-001", "email": "test@example.com"},
        )
        assert resp.status_code == 429
        assert "Too many requests" in resp.json()["detail"]
