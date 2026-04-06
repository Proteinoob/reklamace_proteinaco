import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

from app.models.return_request import ReturnRequest, ReturnItem
from app.models.status_history import StatusHistory
from app.models.enums import ReturnStatus, ReturnReason
from app.schemas.return_schemas import ReturnCreateRequest, ReturnItemRequest
from app.schemas.common import OrderLookupRequest
from app.services.zasilkovna import ZasilkovnaError
from app.services import return_service


# --- Realistic mock data ---

MOCK_ORDER = {
    "order": {
        "code": "OBJ-12345",
        "email": "jan@example.com",
        "billFullName": "Jan Novák",
        "phone": "+420123456789",
        "creationTime": "2026-03-15T10:00:00",
        "items": [
            {
                "code": "WP-CHOCO-1KG",
                "name": "Whey Protein Čokoláda 1kg",
                "amount": 2,
                "itemPrice": {"withVat": 795.0},
            }
        ],
        "invoices": [{"code": "FV-2026-001"}],
    }
}


def _build_create_request() -> ReturnCreateRequest:
    """Helper to build a valid ReturnCreateRequest for tests."""
    return ReturnCreateRequest(
        order_code="OBJ-12345",
        email="jan@example.com",
        name="Jan Novák",
        phone="+420123456789",
        bank_account="CZ1234567890",
        items=[
            ReturnItemRequest(
                product_code="WP-CHOCO-1KG",
                quantity=1,
                reason=ReturnReason.NOT_SATISFIED,
                comment="Neodpovídá očekávání",
            )
        ],
    )


# --- Helper to create a return in DB with a given status ---

def _create_return_in_db(db, status=ReturnStatus.NEW.value, code="RV-2026-0001"):
    """Insert a ReturnRequest + history into the DB."""
    return_req = ReturnRequest(
        code=code,
        order_code="OBJ-12345",
        customer_email="jan@example.com",
        customer_name="Jan Novák",
        customer_phone="+420123456789",
        bank_account="CZ1234567890",
        status=status,
        total_refund_amount=795.0,
    )
    db.add(return_req)
    db.flush()

    item = ReturnItem(
        return_request_id=return_req.id,
        product_code="WP-CHOCO-1KG",
        product_name="Whey Protein Čokoláda 1kg",
        quantity=1,
        unit_price=795.0,
        reason=ReturnReason.NOT_SATISFIED.value,
        refund_amount=795.0,
    )
    db.add(item)

    history = StatusHistory(
        entity_type="return",
        entity_id=return_req.id,
        old_status=None,
        new_status=status,
        changed_by="test",
        note="Test setup",
    )
    db.add(history)
    db.commit()
    return return_req


# ==================== Tests ====================


class TestGenerateReturnCode:
    def test_first_code(self, db_session):
        """No existing codes -> RV-{year}-0001."""
        code = return_service.generate_return_code(db_session)
        year = datetime.now(timezone.utc).year
        assert code == f"RV-{year}-0001"

    def test_sequential(self, db_session):
        """Existing RV-2026-0003 -> next is RV-2026-0004."""
        year = datetime.now(timezone.utc).year
        # Insert an existing return with code 0003
        existing = ReturnRequest(
            code=f"RV-{year}-0003",
            order_code="OBJ-100",
            customer_email="a@b.com",
            customer_name="Test",
        )
        db_session.add(existing)
        db_session.commit()

        code = return_service.generate_return_code(db_session)
        assert code == f"RV-{year}-0004"


class TestCreateReturn:
    @pytest.mark.asyncio
    async def test_success(self, db_session):
        """Full happy path: mock Shoptet + Zásilkovna, verify DB records."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER
        mock_shoptet.__aenter__ = AsyncMock(return_value=mock_shoptet)
        mock_shoptet.__aexit__ = AsyncMock(return_value=False)

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet.return_value = {
            "packet_id": "Z12345",
            "barcode": "Z00012345",
        }
        mock_zasilkovna.__aenter__ = AsyncMock(return_value=mock_zasilkovna)
        mock_zasilkovna.__aexit__ = AsyncMock(return_value=False)

        mock_email = AsyncMock()
        mock_email.send_return_confirmation = AsyncMock(return_value=True)

        with (
            patch(
                "app.services.return_service.ShoptetClient",
                return_value=mock_shoptet,
            ),
            patch(
                "app.services.return_service.ZasilkovnaClient",
                return_value=mock_zasilkovna,
            ),
            patch(
                "app.services.return_service.EmailService",
                return_value=mock_email,
            ),
        ):
            request = _build_create_request()
            response = await return_service.create_return(request, db_session)

        assert response.code.startswith("RV-")
        assert response.label_url is not None
        assert "Z12345" in response.label_url

        # Verify DB record
        saved = db_session.query(ReturnRequest).filter_by(code=response.code).first()
        assert saved is not None
        assert saved.status == ReturnStatus.WAITING_FOR_DELIVERY.value
        assert saved.total_refund_amount == 795.0
        assert len(saved.items) == 1
        assert saved.items[0].product_name == "Whey Protein Čokoláda 1kg"

        # Verify status history
        history = (
            db_session.query(StatusHistory)
            .filter_by(entity_type="return", entity_id=saved.id)
            .order_by(StatusHistory.id.asc())
            .all()
        )
        assert len(history) == 2
        assert history[0].new_status == ReturnStatus.NEW.value
        assert history[1].new_status == ReturnStatus.WAITING_FOR_DELIVERY.value

    @pytest.mark.asyncio
    async def test_email_failure_doesnt_crash(self, db_session):
        """Email send failure should not prevent return creation."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER
        mock_shoptet.__aenter__ = AsyncMock(return_value=mock_shoptet)
        mock_shoptet.__aexit__ = AsyncMock(return_value=False)

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet.return_value = {
            "packet_id": "Z999",
            "barcode": "Z000999",
        }
        mock_zasilkovna.__aenter__ = AsyncMock(return_value=mock_zasilkovna)
        mock_zasilkovna.__aexit__ = AsyncMock(return_value=False)

        mock_email = AsyncMock()
        mock_email.send_return_confirmation = AsyncMock(
            side_effect=Exception("SMTP connection refused")
        )

        with (
            patch(
                "app.services.return_service.ShoptetClient",
                return_value=mock_shoptet,
            ),
            patch(
                "app.services.return_service.ZasilkovnaClient",
                return_value=mock_zasilkovna,
            ),
            patch(
                "app.services.return_service.EmailService",
                return_value=mock_email,
            ),
        ):
            request = _build_create_request()
            response = await return_service.create_return(request, db_session)

        # Return was still created despite email failure
        assert response.code.startswith("RV-")
        saved = db_session.query(ReturnRequest).filter_by(code=response.code).first()
        assert saved is not None

    @pytest.mark.asyncio
    async def test_zasilkovna_failure_doesnt_crash(self, db_session):
        """Zásilkovna error should not prevent return creation; label_url=None."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER
        mock_shoptet.__aenter__ = AsyncMock(return_value=mock_shoptet)
        mock_shoptet.__aexit__ = AsyncMock(return_value=False)

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet = AsyncMock(
            side_effect=ZasilkovnaError("API error")
        )
        mock_zasilkovna.__aenter__ = AsyncMock(return_value=mock_zasilkovna)
        mock_zasilkovna.__aexit__ = AsyncMock(return_value=False)

        mock_email = AsyncMock()
        mock_email.send_return_confirmation = AsyncMock(return_value=True)

        with (
            patch(
                "app.services.return_service.ShoptetClient",
                return_value=mock_shoptet,
            ),
            patch(
                "app.services.return_service.ZasilkovnaClient",
                return_value=mock_zasilkovna,
            ),
            patch(
                "app.services.return_service.EmailService",
                return_value=mock_email,
            ),
        ):
            request = _build_create_request()
            response = await return_service.create_return(request, db_session)

        assert response.code.startswith("RV-")
        assert response.label_url is None

        saved = db_session.query(ReturnRequest).filter_by(code=response.code).first()
        assert saved is not None
        assert saved.shipping_label_url is None


class TestReceiveReturn:
    def test_receive_from_waiting(self, db_session):
        """From WAITING_FOR_DELIVERY -> RECEIVED_INSPECTING."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.WAITING_FOR_DELIVERY.value
        )

        result = return_service.receive_return(
            return_req.id, db_session, "admin_karel"
        )

        assert result.status == ReturnStatus.RECEIVED_INSPECTING.value

        # Verify new history entry
        history = (
            db_session.query(StatusHistory)
            .filter_by(entity_type="return", entity_id=return_req.id)
            .order_by(StatusHistory.id.desc())
            .first()
        )
        assert history.new_status == ReturnStatus.RECEIVED_INSPECTING.value
        assert history.changed_by == "admin_karel"

    def test_receive_invalid_status(self, db_session):
        """Receiving from NEW should raise ValueError."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.NEW.value
        )

        with pytest.raises(ValueError, match="Nelze přejít"):
            return_service.receive_return(
                return_req.id, db_session, "admin_karel"
            )


class TestApproveReturn:
    @pytest.mark.asyncio
    async def test_approve(self, db_session):
        """From RECEIVED_INSPECTING -> APPROVED -> auto REFUND_READY."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.RECEIVED_INSPECTING.value
        )

        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER
        mock_shoptet.create_credit_note.return_value = {"code": "DN-2026-001"}
        mock_shoptet.__aenter__ = AsyncMock(return_value=mock_shoptet)
        mock_shoptet.__aexit__ = AsyncMock(return_value=False)

        mock_email = AsyncMock()
        mock_email.send_status_change = AsyncMock(return_value=True)

        with (
            patch(
                "app.services.return_service.ShoptetClient",
                return_value=mock_shoptet,
            ),
            patch(
                "app.services.return_service.EmailService",
                return_value=mock_email,
            ),
        ):
            result = await return_service.approve_return(
                return_req.id, db_session, "admin_jana"
            )

        # Should auto-transition to REFUND_READY
        assert result.status == ReturnStatus.REFUND_READY.value

        # Verify history has both transitions
        history = (
            db_session.query(StatusHistory)
            .filter_by(entity_type="return", entity_id=return_req.id)
            .order_by(StatusHistory.id.asc())
            .all()
        )
        statuses = [h.new_status for h in history]
        assert ReturnStatus.APPROVED.value in statuses
        assert ReturnStatus.REFUND_READY.value in statuses


class TestRejectReturn:
    def test_reject(self, db_session):
        """Reject with reason saved as admin_note."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.RECEIVED_INSPECTING.value
        )

        reason = "Zboží bylo poškozeno zákazníkem"
        result = return_service.reject_return(
            return_req.id, reason, db_session, "admin_petr"
        )

        assert result.status == ReturnStatus.REJECTED.value
        assert result.admin_note == reason

        history = (
            db_session.query(StatusHistory)
            .filter_by(entity_type="return", entity_id=return_req.id)
            .order_by(StatusHistory.id.desc())
            .first()
        )
        assert history.new_status == ReturnStatus.REJECTED.value
        assert history.changed_by == "admin_petr"


class TestMarkRefunded:
    def test_mark_refunded(self, db_session):
        """From REFUND_READY -> COMPLETED."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.REFUND_READY.value
        )

        result = return_service.mark_refunded(
            return_req.id, db_session, "admin_eva"
        )

        assert result.status == ReturnStatus.COMPLETED.value

        history = (
            db_session.query(StatusHistory)
            .filter_by(entity_type="return", entity_id=return_req.id)
            .order_by(StatusHistory.id.desc())
            .first()
        )
        assert history.new_status == ReturnStatus.COMPLETED.value
        assert history.changed_by == "admin_eva"


class TestGetReturnDetail:
    def test_all_fields_populated(self, db_session):
        """Verify the detail response has all expected fields."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.WAITING_FOR_DELIVERY.value
        )

        detail = return_service.get_return_detail(return_req.id, db_session)

        assert detail.id == return_req.id
        assert detail.code == "RV-2026-0001"
        assert detail.order_code == "OBJ-12345"
        assert detail.customer_email == "jan@example.com"
        assert detail.customer_name == "Jan Novák"
        assert detail.customer_phone == "+420123456789"
        assert detail.bank_account == "CZ1234567890"
        assert detail.status == ReturnStatus.WAITING_FOR_DELIVERY.value
        assert detail.total_refund_amount == 795.0
        assert len(detail.items) == 1
        assert detail.items[0].product_code == "WP-CHOCO-1KG"
        assert len(detail.status_history) >= 1
        assert detail.days_in_current_status >= 0
        assert detail.created_at is not None
        assert detail.updated_at is not None


class TestListReturns:
    def test_with_status_filter(self, db_session):
        """Filter by status should only return matching returns."""
        _create_return_in_db(
            db_session,
            status=ReturnStatus.WAITING_FOR_DELIVERY.value,
            code="RV-2026-0001",
        )
        _create_return_in_db(
            db_session,
            status=ReturnStatus.COMPLETED.value,
            code="RV-2026-0002",
        )

        result = return_service.list_returns(
            db_session, status=ReturnStatus.WAITING_FOR_DELIVERY.value
        )

        assert result.total == 1
        assert result.items[0].code == "RV-2026-0001"

    def test_with_order_code_filter(self, db_session):
        """Filter by order_code."""
        _create_return_in_db(
            db_session,
            status=ReturnStatus.NEW.value,
            code="RV-2026-0010",
        )

        result = return_service.list_returns(
            db_session, order_code="OBJ-12345"
        )

        assert result.total == 1
        assert result.items[0].order_code == "OBJ-12345"

    def test_pagination(self, db_session):
        """Page and page_size should work correctly."""
        for i in range(5):
            _create_return_in_db(
                db_session,
                status=ReturnStatus.NEW.value,
                code=f"RV-2026-{i+1:04d}",
            )

        result = return_service.list_returns(
            db_session, page=1, page_size=2
        )
        assert result.total == 5
        assert len(result.items) == 2
        assert result.page == 1
        assert result.page_size == 2


class TestGetReturnByCode:
    def test_wrong_email_raises(self, db_session):
        """Looking up a return with wrong email should raise ValueError."""
        _create_return_in_db(db_session, status=ReturnStatus.NEW.value)

        with pytest.raises(ValueError, match="Email neodpovídá"):
            return_service.get_return_by_code(
                "RV-2026-0001", "wrong@email.com", db_session
            )

    def test_correct_email(self, db_session):
        """Valid code + email returns tracking response."""
        return_req = _create_return_in_db(
            db_session, status=ReturnStatus.WAITING_FOR_DELIVERY.value
        )

        result = return_service.get_return_by_code(
            "RV-2026-0001", "jan@example.com", db_session
        )

        assert result.code == "RV-2026-0001"
        assert result.status == ReturnStatus.WAITING_FOR_DELIVERY.value
        assert result.status_label == "Čeká na doručení"
        assert len(result.items) == 1
