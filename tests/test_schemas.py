import pytest
from pydantic import ValidationError

from app.schemas.common import OrderLookupRequest
from app.schemas.return_schemas import (
    ReturnCreateRequest,
    ReturnItemRequest,
)
from app.schemas.complaint_schemas import (
    AdminRejectRequest,
    ComplaintCreateRequest,
    ComplaintItemRequest,
)
from app.models.enums import PreferredResolution, ReturnReason


class TestOrderLookupRequest:
    def test_order_lookup_request_valid(self):
        req = OrderLookupRequest(order_code="OBJ-12345", email="Test@Example.com")
        assert req.order_code == "OBJ-12345"
        assert req.email == "test@example.com"  # lowered + stripped

    def test_order_lookup_request_invalid_email(self):
        with pytest.raises(ValidationError) as exc_info:
            OrderLookupRequest(order_code="OBJ-12345", email="not-an-email")
        assert "Invalid email format" in str(exc_info.value)


class TestReturnSchemas:
    def _valid_return_item(self, **overrides):
        defaults = {
            "product_code": "PROD-001",
            "quantity": 2,
            "reason": ReturnReason.NOT_SATISFIED,
            "comment": "Did not like the taste",
        }
        defaults.update(overrides)
        return defaults

    def test_return_create_request_valid(self):
        req = ReturnCreateRequest(
            order_code="OBJ-12345",
            email="customer@example.com",
            name="Jan Novak",
            phone="+420123456789",
            bank_account="CZ1234567890",
            items=[ReturnItemRequest(**self._valid_return_item())],
        )
        assert req.order_code == "OBJ-12345"
        assert req.name == "Jan Novak"
        assert len(req.items) == 1
        assert req.items[0].reason == ReturnReason.NOT_SATISFIED

    def test_return_create_request_empty_items(self):
        with pytest.raises(ValidationError) as exc_info:
            ReturnCreateRequest(
                order_code="OBJ-12345",
                email="customer@example.com",
                name="Jan Novak",
                items=[],
            )
        assert "At least one item is required" in str(exc_info.value)

    def test_return_create_request_invalid_quantity(self):
        with pytest.raises(ValidationError) as exc_info:
            ReturnItemRequest(
                product_code="PROD-001",
                quantity=0,
                reason=ReturnReason.ORDERED_WRONG,
            )
        assert "Quantity must be at least 1" in str(exc_info.value)

    def test_return_item_request_enum_validation(self):
        with pytest.raises(ValidationError):
            ReturnItemRequest(
                product_code="PROD-001",
                quantity=1,
                reason="invalid_reason_value",
            )


class TestComplaintSchemas:
    def _valid_complaint_item(self, **overrides):
        defaults = {
            "product_code": "PROD-002",
            "quantity": 1,
            "problem_description": "Product smells bad",
            "preferred_resolution": PreferredResolution.REFUND,
        }
        defaults.update(overrides)
        return defaults

    def test_complaint_create_request_valid(self):
        req = ComplaintCreateRequest(
            order_code="OBJ-99999",
            email="Zakaznik@Test.Cz",
            name="Petr Svoboda",
            phone="+420777888999",
            bank_account="CZ9876543210",
            items=[ComplaintItemRequest(**self._valid_complaint_item())],
        )
        assert req.email == "zakaznik@test.cz"
        assert len(req.items) == 1
        assert req.items[0].problem_description == "Product smells bad"

    def test_complaint_item_request_with_optional_fields(self):
        item = ComplaintItemRequest(
            product_code="PROD-003",
            quantity=1,
            problem_description="Found foreign object",
            doses_taken=3,
            discovery_date="2026-04-01",
            preferred_resolution=PreferredResolution.NEW_PRODUCT,
        )
        assert item.doses_taken == 3
        assert item.discovery_date == "2026-04-01"
        assert item.preferred_resolution == PreferredResolution.NEW_PRODUCT

    def test_complaint_item_request_without_optional_fields(self):
        item = ComplaintItemRequest(
            product_code="PROD-003",
            quantity=1,
            problem_description="Bad taste",
        )
        assert item.doses_taken is None
        assert item.discovery_date is None
        assert item.preferred_resolution == PreferredResolution.REFUND


class TestAdminSchemas:
    def test_admin_reject_request_requires_reason(self):
        # Empty string after strip should fail
        with pytest.raises(ValidationError) as exc_info:
            AdminRejectRequest(reason="   ")
        assert "Reason is required" in str(exc_info.value)

    def test_admin_reject_request_valid(self):
        req = AdminRejectRequest(reason="Product was used, cannot accept return")
        assert req.reason == "Product was used, cannot accept return"
