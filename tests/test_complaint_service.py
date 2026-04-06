import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.complaint import Complaint, ComplaintItem, ComplaintPhoto
from app.models.status_history import StatusHistory
from app.models.enums import ComplaintStatus, PreferredResolution
from app.schemas.complaint_schemas import ComplaintCreateRequest, ComplaintItemRequest
from app.services.complaint_service import (
    generate_complaint_code,
    create_complaint,
    upload_photos,
    supplement_complaint,
    request_more_info,
    start_assessment,
    approve_complaint,
    reject_complaint,
    resolve_complaint,
    get_complaint_detail,
    get_complaint_by_code,
    list_complaints,
    COMPLAINT_TRANSITIONS,
    _validate_transition,
)


# --------------- Mock data ---------------

MOCK_ORDER = {
    "order": {
        "code": "OBJ-12345",
        "email": "jan@example.com",
        "billFullName": "Jan Novak",
        "phone": "+420123456789",
        "creationTime": "2026-03-15T10:00:00",
        "items": [
            {
                "code": "WP-CHOCO-1KG",
                "name": "Whey Protein Cokolada 1kg",
                "amount": 1,
                "itemPrice": {"withVat": 795.0},
            }
        ],
        "invoices": [{"code": "FV-2026-001"}],
    }
}


# --------------- Helpers ---------------

def _create_complaint_in_db(db_session, code="RE-2026-0001", status=ComplaintStatus.NEW.value):
    """Helper to create a complaint directly in DB for testing."""
    complaint = Complaint(
        code=code,
        order_code="OBJ-12345",
        customer_email="jan@example.com",
        customer_name="Jan Novak",
        customer_phone="+420123456789",
        status=status,
        preferred_resolution=PreferredResolution.REFUND.value,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(complaint)
    db_session.flush()

    item = ComplaintItem(
        complaint_id=complaint.id,
        product_code="WP-CHOCO-1KG",
        product_name="Whey Protein Cokolada 1kg",
        quantity=1,
        unit_price=795.0,
        problem_description="Produkt byl plesnivej",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(complaint)
    return complaint


def _build_create_request():
    """Build a standard ComplaintCreateRequest for testing."""
    return ComplaintCreateRequest(
        order_code="OBJ-12345",
        email="jan@example.com",
        name="Jan Novak",
        phone="+420123456789",
        items=[
            ComplaintItemRequest(
                product_code="WP-CHOCO-1KG",
                quantity=1,
                problem_description="Produkt byl plesnivej",
                preferred_resolution=PreferredResolution.REFUND,
            )
        ],
    )


# --------------- Tests: generate_complaint_code ---------------


class TestGenerateComplaintCode:
    def test_generate_complaint_code_first(self, db_session):
        """First code of the year should be RE-{year}-0001."""
        code = generate_complaint_code(db_session)
        year = datetime.now(timezone.utc).year
        assert code == f"RE-{year}-0001"

    def test_generate_complaint_code_sequential(self, db_session):
        """Subsequent codes should increment sequentially."""
        year = datetime.now(timezone.utc).year
        _create_complaint_in_db(db_session, code=f"RE-{year}-0001")
        code = generate_complaint_code(db_session)
        assert code == f"RE-{year}-0002"

        _create_complaint_in_db(db_session, code=f"RE-{year}-0002")
        code = generate_complaint_code(db_session)
        assert code == f"RE-{year}-0003"


# --------------- Tests: create_complaint ---------------


class TestCreateComplaint:
    @pytest.mark.asyncio
    async def test_create_complaint_success(self, db_session):
        """Full creation flow with mocked external services."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet.return_value = {
            "packet_id": "Z123456",
            "barcode": "Z123456789",
        }

        mock_email = AsyncMock()
        mock_email.send_complaint_confirmation.return_value = True

        request = _build_create_request()

        result = await create_complaint(
            request, db_session,
            shoptet_client=mock_shoptet,
            zasilkovna_client=mock_zasilkovna,
            email_service=mock_email,
        )

        assert result.code.startswith("RE-")
        assert result.label_url is not None
        assert "Z123456" in result.label_url

        # Verify DB records
        complaint = db_session.query(Complaint).filter(
            Complaint.code == result.code
        ).first()
        assert complaint is not None
        assert complaint.order_code == "OBJ-12345"
        assert complaint.customer_email == "jan@example.com"
        assert len(complaint.items) == 1
        assert complaint.items[0].product_code == "WP-CHOCO-1KG"
        assert complaint.items[0].unit_price == 795.0

        # Verify Shoptet was called
        mock_shoptet.get_order.assert_called_once_with("OBJ-12345")

        # Verify email was sent
        mock_email.send_complaint_confirmation.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_complaint_auto_transitions_to_waiting(self, db_session):
        """After creation, status should be WAITING_FOR_ASSESSMENT, not NEW."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet.return_value = {
            "packet_id": "Z1", "barcode": "BC1",
        }

        mock_email = AsyncMock()
        mock_email.send_complaint_confirmation.return_value = True

        request = _build_create_request()

        result = await create_complaint(
            request, db_session,
            shoptet_client=mock_shoptet,
            zasilkovna_client=mock_zasilkovna,
            email_service=mock_email,
        )

        complaint = db_session.query(Complaint).filter(
            Complaint.code == result.code
        ).first()
        assert complaint.status == ComplaintStatus.WAITING_FOR_ASSESSMENT.value

        # Verify status history has both transitions
        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .order_by(StatusHistory.id.asc())
            .all()
        )
        assert len(history) == 2
        assert history[0].new_status == ComplaintStatus.NEW.value
        assert history[1].old_status == ComplaintStatus.NEW.value
        assert history[1].new_status == ComplaintStatus.WAITING_FOR_ASSESSMENT.value

    @pytest.mark.asyncio
    async def test_create_complaint_generates_zasilkovna_label(self, db_session):
        """Label URL should be saved on the complaint record."""
        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER

        mock_zasilkovna = AsyncMock()
        mock_zasilkovna.create_return_packet.return_value = {
            "packet_id": "Z999",
            "barcode": "ZBC999",
        }

        mock_email = AsyncMock()
        mock_email.send_complaint_confirmation.return_value = True

        request = _build_create_request()

        result = await create_complaint(
            request, db_session,
            shoptet_client=mock_shoptet,
            zasilkovna_client=mock_zasilkovna,
            email_service=mock_email,
        )

        complaint = db_session.query(Complaint).filter(
            Complaint.code == result.code
        ).first()
        assert complaint.shipping_label_url is not None
        assert "Z999" in complaint.shipping_label_url
        assert complaint.tracking_number == "ZBC999"

        mock_zasilkovna.create_return_packet.assert_called_once()


# --------------- Tests: upload_photos ---------------


class TestUploadPhotos:
    @pytest.mark.asyncio
    async def test_upload_photos(self, db_session):
        """Successfully upload photos for a complaint."""
        complaint = _create_complaint_in_db(db_session)

        fake_image_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header

        with patch("app.services.complaint_service.save_photo") as mock_save:
            mock_photo = ComplaintPhoto(
                id=1,
                complaint_id=complaint.id,
                file_path="/uploads/test.jpg",
                original_filename="test.jpg",
            )
            mock_save.return_value = mock_photo

            photos = await upload_photos(
                complaint_code=complaint.code,
                email="jan@example.com",
                files=[(fake_image_data, "test.jpg", "image/jpeg")],
                db=db_session,
            )

            assert len(photos) == 1
            mock_save.assert_called_once_with(
                complaint_id=complaint.id,
                file_data=fake_image_data,
                original_filename="test.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_upload_photos_wrong_email(self, db_session):
        """Should raise ValueError if email doesn't match."""
        complaint = _create_complaint_in_db(db_session)

        with pytest.raises(ValueError, match="Email does not match"):
            await upload_photos(
                complaint_code=complaint.code,
                email="wrong@example.com",
                files=[(b"data", "test.jpg", "image/jpeg")],
                db=db_session,
            )

    @pytest.mark.asyncio
    async def test_upload_photos_not_found(self, db_session):
        """Should raise ValueError if complaint doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await upload_photos(
                complaint_code="RE-9999-9999",
                email="jan@example.com",
                files=[(b"data", "test.jpg", "image/jpeg")],
                db=db_session,
            )


# --------------- Tests: supplement_complaint ---------------


class TestSupplementComplaint:
    @pytest.mark.asyncio
    async def test_supplement_complaint_from_need_more_info(self, db_session):
        """Customer can supplement when status is NEED_MORE_INFO."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.NEED_MORE_INFO.value,
        )

        mock_email = AsyncMock()
        mock_email.send_status_change.return_value = True

        result = await supplement_complaint(
            complaint_code=complaint.code,
            email="jan@example.com",
            message="Zde jsou doplnujici informace.",
            db=db_session,
            email_service=mock_email,
        )

        assert result.status == ComplaintStatus.WAITING_FOR_ASSESSMENT.value

        # Verify status history
        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .all()
        )
        assert len(history) == 1
        assert history[0].old_status == ComplaintStatus.NEED_MORE_INFO.value
        assert history[0].new_status == ComplaintStatus.WAITING_FOR_ASSESSMENT.value
        assert history[0].note == "Zde jsou doplnujici informace."

        mock_email.send_status_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_supplement_complaint_wrong_status(self, db_session):
        """Should raise ValueError if not in NEED_MORE_INFO status."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
        )

        with pytest.raises(ValueError, match="Cannot supplement"):
            await supplement_complaint(
                complaint_code=complaint.code,
                email="jan@example.com",
                message="Some message",
                db=db_session,
            )


# --------------- Tests: request_more_info ---------------


class TestRequestMoreInfo:
    def test_request_more_info(self, db_session):
        """Admin can request more info from WAITING_FOR_ASSESSMENT."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
        )

        result = request_more_info(
            complaint_id=complaint.id,
            message="Poslete prosim fotky.",
            db=db_session,
            admin_user="admin_petr",
        )

        assert result.status == ComplaintStatus.NEED_MORE_INFO.value

        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .all()
        )
        assert len(history) == 1
        assert history[0].changed_by == "admin_petr"
        assert history[0].note == "Poslete prosim fotky."


# --------------- Tests: start_assessment ---------------


class TestStartAssessment:
    def test_start_assessment(self, db_session):
        """Admin transitions to ASSESSING from WAITING_FOR_ASSESSMENT."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
        )

        result = start_assessment(
            complaint_id=complaint.id,
            db=db_session,
            admin_user="admin_jana",
        )

        assert result.status == ComplaintStatus.ASSESSING.value

        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .all()
        )
        assert len(history) == 1
        assert history[0].old_status == ComplaintStatus.WAITING_FOR_ASSESSMENT.value
        assert history[0].new_status == ComplaintStatus.ASSESSING.value
        assert history[0].changed_by == "admin_jana"


# --------------- Tests: approve_complaint ---------------


class TestApproveComplaint:
    @pytest.mark.asyncio
    async def test_approve_complaint_with_refund(self, db_session):
        """Approving with REFUND resolution should create a credit note."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.ASSESSING.value,
        )

        mock_shoptet = AsyncMock()
        mock_shoptet.get_order.return_value = MOCK_ORDER
        mock_shoptet.create_credit_note.return_value = {"code": "CN-001"}

        mock_email = AsyncMock()
        mock_email.send_status_change.return_value = True

        result = await approve_complaint(
            complaint_id=complaint.id,
            resolution=PreferredResolution.REFUND,
            note="Opravnena reklamace",
            db=db_session,
            admin_user="admin_petr",
            shoptet_client=mock_shoptet,
            email_service=mock_email,
        )

        assert result.status == ComplaintStatus.APPROVED.value
        assert result.preferred_resolution == PreferredResolution.REFUND.value
        assert result.admin_note == "Opravnena reklamace"

        # Verify credit note was created
        mock_shoptet.get_order.assert_called_once_with("OBJ-12345")
        mock_shoptet.create_credit_note.assert_called_once_with("FV-2026-001")

        mock_email.send_status_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_complaint_with_discount(self, db_session):
        """Approving with DISCOUNT resolution should NOT create credit note."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.ASSESSING.value,
        )

        mock_shoptet = AsyncMock()
        mock_email = AsyncMock()
        mock_email.send_status_change.return_value = True

        result = await approve_complaint(
            complaint_id=complaint.id,
            resolution=PreferredResolution.DISCOUNT,
            note="Sleva 20%",
            db=db_session,
            admin_user="admin_jana",
            shoptet_client=mock_shoptet,
            email_service=mock_email,
        )

        assert result.status == ComplaintStatus.APPROVED.value
        assert result.preferred_resolution == PreferredResolution.DISCOUNT.value

        # Shoptet should NOT have been called (no credit note for discount)
        mock_shoptet.get_order.assert_not_called()
        mock_shoptet.create_credit_note.assert_not_called()


# --------------- Tests: reject_complaint ---------------


class TestRejectComplaint:
    def test_reject_complaint(self, db_session):
        """Admin can reject from ASSESSING."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.ASSESSING.value,
        )

        result = reject_complaint(
            complaint_id=complaint.id,
            reason="Neodpovida podminkam reklamace",
            db=db_session,
            admin_user="admin_petr",
        )

        assert result.status == ComplaintStatus.REJECTED.value
        assert result.admin_note == "Neodpovida podminkam reklamace"

        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .all()
        )
        assert len(history) == 1
        assert "Zamitnuto" in history[0].note


# --------------- Tests: resolve_complaint ---------------


class TestResolveComplaint:
    @pytest.mark.asyncio
    async def test_resolve_complaint(self, db_session):
        """Admin resolves from APPROVED to RESOLVED."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.APPROVED.value,
        )

        mock_email = AsyncMock()
        mock_email.send_resolution.return_value = True

        result = await resolve_complaint(
            complaint_id=complaint.id,
            db=db_session,
            admin_user="admin_jana",
            email_service=mock_email,
        )

        assert result.status == ComplaintStatus.RESOLVED.value

        history = (
            db_session.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "complaint",
                StatusHistory.entity_id == complaint.id,
            )
            .all()
        )
        assert len(history) == 1
        assert history[0].old_status == ComplaintStatus.APPROVED.value
        assert history[0].new_status == ComplaintStatus.RESOLVED.value

        mock_email.send_resolution.assert_called_once()


# --------------- Tests: invalid transitions ---------------


class TestInvalidStatusTransition:
    def test_invalid_status_transition(self, db_session):
        """Attempting an invalid transition should raise ValueError."""
        # NEW -> ASSESSING is not valid (must go through WAITING_FOR_ASSESSMENT)
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.NEW.value,
        )

        with pytest.raises(ValueError, match="Invalid status transition"):
            start_assessment(
                complaint_id=complaint.id,
                db=db_session,
                admin_user="admin_petr",
            )

    def test_rejected_is_terminal(self, db_session):
        """REJECTED allows no further transitions."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.REJECTED.value,
        )

        with pytest.raises(ValueError, match="Invalid status transition"):
            request_more_info(
                complaint_id=complaint.id,
                message="test",
                db=db_session,
                admin_user="admin",
            )

    def test_resolved_is_terminal(self, db_session):
        """RESOLVED allows no further transitions."""
        complaint = _create_complaint_in_db(
            db_session, status=ComplaintStatus.RESOLVED.value,
        )

        with pytest.raises(ValueError, match="Invalid status transition"):
            request_more_info(
                complaint_id=complaint.id,
                message="test",
                db=db_session,
                admin_user="admin",
            )


# --------------- Tests: get_complaint_detail ---------------


class TestGetComplaintDetail:
    def test_get_complaint_detail(self, db_session):
        """Should return full detail with items, photos, and status history."""
        complaint = _create_complaint_in_db(db_session)

        # Add status history
        sh = StatusHistory(
            entity_type="complaint",
            entity_id=complaint.id,
            old_status=None,
            new_status=ComplaintStatus.NEW.value,
            changed_by="customer",
            note="Vytvoreno",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(sh)
        db_session.commit()

        detail = get_complaint_detail(complaint.id, db_session)

        assert detail.id == complaint.id
        assert detail.code == complaint.code
        assert detail.order_code == "OBJ-12345"
        assert detail.customer_email == "jan@example.com"
        assert detail.customer_name == "Jan Novak"
        assert len(detail.items) == 1
        assert detail.items[0].product_code == "WP-CHOCO-1KG"
        assert len(detail.status_history) == 1
        assert detail.status_history[0].changed_by == "customer"
        assert detail.days_in_current_status >= 0

    def test_get_complaint_detail_not_found(self, db_session):
        """Should raise ValueError for non-existent complaint."""
        with pytest.raises(ValueError, match="not found"):
            get_complaint_detail(99999, db_session)


# --------------- Tests: get_complaint_by_code ---------------


class TestGetComplaintByCode:
    def test_get_complaint_by_code(self, db_session):
        """Customer tracking lookup by code + email."""
        complaint = _create_complaint_in_db(db_session)

        result = get_complaint_by_code(
            code=complaint.code,
            email="jan@example.com",
            db=db_session,
        )

        assert result.code == complaint.code
        assert result.status == complaint.status
        assert result.status_label is not None
        assert len(result.items) == 1

    def test_get_complaint_by_code_wrong_email(self, db_session):
        """Should raise ValueError if email doesn't match."""
        complaint = _create_complaint_in_db(db_session)

        with pytest.raises(ValueError, match="Email does not match"):
            get_complaint_by_code(
                code=complaint.code,
                email="wrong@example.com",
                db=db_session,
            )


# --------------- Tests: list_complaints ---------------


class TestListComplaints:
    def test_list_complaints_with_filters(self, db_session):
        """Paginated list with status filter."""
        _create_complaint_in_db(
            db_session, code="RE-2026-0001",
            status=ComplaintStatus.NEW.value,
        )
        _create_complaint_in_db(
            db_session, code="RE-2026-0002",
            status=ComplaintStatus.ASSESSING.value,
        )
        _create_complaint_in_db(
            db_session, code="RE-2026-0003",
            status=ComplaintStatus.NEW.value,
        )

        # No filter - returns all
        result = list_complaints(db_session)
        assert result.total == 3
        assert len(result.items) == 3

        # Filter by status
        result = list_complaints(db_session, status=ComplaintStatus.NEW.value)
        assert result.total == 2
        assert all(i.status == ComplaintStatus.NEW.value for i in result.items)

        # Filter by order_code
        result = list_complaints(db_session, order_code="OBJ-12345")
        assert result.total == 3

        # Pagination
        result = list_complaints(db_session, page=1, page_size=2)
        assert result.total == 3
        assert len(result.items) == 2
        assert result.page == 1
        assert result.page_size == 2

    def test_list_complaints_empty(self, db_session):
        """Empty list returns zero results."""
        result = list_complaints(db_session)
        assert result.total == 0
        assert len(result.items) == 0
