from datetime import date, datetime

import pytest

from app.models import (
    ReturnStatus,
    ComplaintStatus,
    ReturnReason,
    PreferredResolution,
    ReturnRequest,
    ReturnItem,
    Complaint,
    ComplaintItem,
    ComplaintPhoto,
    StatusHistory,
)


# --------------- Enum tests ---------------


class TestEnums:
    def test_return_status_values(self):
        assert ReturnStatus.NEW.value == "new"
        assert ReturnStatus.WAITING_FOR_DELIVERY.value == "waiting_for_delivery"
        assert ReturnStatus.RECEIVED_INSPECTING.value == "received_inspecting"
        assert ReturnStatus.APPROVED.value == "approved"
        assert ReturnStatus.REJECTED.value == "rejected"
        assert ReturnStatus.REFUND_READY.value == "refund_ready"
        assert ReturnStatus.COMPLETED.value == "completed"
        assert len(ReturnStatus) == 7

    def test_complaint_status_values(self):
        assert ComplaintStatus.NEW.value == "new"
        assert ComplaintStatus.WAITING_FOR_ASSESSMENT.value == "waiting_for_assessment"
        assert ComplaintStatus.NEED_MORE_INFO.value == "need_more_info"
        assert ComplaintStatus.ASSESSING.value == "assessing"
        assert ComplaintStatus.APPROVED.value == "approved"
        assert ComplaintStatus.REJECTED.value == "rejected"
        assert ComplaintStatus.RESOLVED.value == "resolved"
        assert len(ComplaintStatus) == 7

    def test_return_reason_values(self):
        assert ReturnReason.ORDERED_WRONG.value == "ordered_wrong"
        assert ReturnReason.NOT_SATISFIED.value == "not_satisfied"
        assert ReturnReason.OTHER.value == "other"
        assert len(ReturnReason) == 3

    def test_preferred_resolution_values(self):
        assert PreferredResolution.DISCOUNT.value == "discount"
        assert PreferredResolution.NEW_PRODUCT.value == "new_product"
        assert PreferredResolution.REFUND.value == "refund"
        assert PreferredResolution.OTHER.value == "other"
        assert len(PreferredResolution) == 4

    def test_enums_are_str_subclass(self):
        """Enum values should be usable as strings."""
        assert isinstance(ReturnStatus.NEW, str)
        assert isinstance(ComplaintStatus.NEW, str)
        assert isinstance(ReturnReason.OTHER, str)
        assert isinstance(PreferredResolution.REFUND, str)


# --------------- ReturnRequest tests ---------------


class TestReturnRequest:
    def test_create_return_request(self, db_session):
        request = ReturnRequest(
            code="RV-2026-0001",
            order_code="OBJ-12345",
            customer_email="jan@example.com",
            customer_name="Jan Novak",
        )
        db_session.add(request)
        db_session.commit()

        saved = db_session.query(ReturnRequest).first()
        assert saved is not None
        assert saved.code == "RV-2026-0001"
        assert saved.order_code == "OBJ-12345"
        assert saved.customer_email == "jan@example.com"
        assert saved.customer_name == "Jan Novak"

    def test_return_request_default_values(self, db_session):
        request = ReturnRequest(
            code="RV-2026-0002",
            order_code="OBJ-99999",
            customer_email="test@test.com",
            customer_name="Test User",
        )
        db_session.add(request)
        db_session.commit()

        saved = db_session.query(ReturnRequest).first()
        assert saved.eshop_id == "228312"
        assert saved.status == ReturnStatus.NEW.value
        assert saved.total_refund_amount == 0
        assert saved.customer_phone is None
        assert saved.bank_account is None
        assert saved.shipping_label_url is None
        assert saved.tracking_number is None
        assert saved.admin_note is None

    def test_return_request_code_format(self, db_session):
        request = ReturnRequest(
            code="RV-2026-0001",
            order_code="OBJ-100",
            customer_email="a@b.com",
            customer_name="A",
        )
        db_session.add(request)
        db_session.commit()

        saved = db_session.query(ReturnRequest).first()
        assert saved.code.startswith("RV-")

    def test_return_request_with_items(self, db_session):
        request = ReturnRequest(
            code="RV-2026-0003",
            order_code="OBJ-500",
            customer_email="items@test.com",
            customer_name="Items Test",
        )
        item1 = ReturnItem(
            product_code="PROD-001",
            product_name="Whey Protein 1kg",
            quantity=2,
            unit_price=599.0,
            reason=ReturnReason.ORDERED_WRONG.value,
            refund_amount=1198.0,
        )
        item2 = ReturnItem(
            product_code="PROD-002",
            product_name="Creatine 500g",
            quantity=1,
            unit_price=349.0,
            reason=ReturnReason.NOT_SATISFIED.value,
            comment="Spatna chut",
            refund_amount=349.0,
        )
        request.items = [item1, item2]
        db_session.add(request)
        db_session.commit()

        saved = db_session.query(ReturnRequest).first()
        assert len(saved.items) == 2
        assert saved.items[0].product_code == "PROD-001"
        assert saved.items[1].product_code == "PROD-002"
        assert saved.items[1].comment == "Spatna chut"

    def test_return_item_relationship(self, db_session):
        request = ReturnRequest(
            code="RV-2026-0004",
            order_code="OBJ-600",
            customer_email="rel@test.com",
            customer_name="Rel Test",
        )
        item = ReturnItem(
            product_code="PROD-010",
            product_name="BCAA 300g",
            unit_price=299.0,
            reason=ReturnReason.OTHER.value,
            refund_amount=299.0,
        )
        request.items.append(item)
        db_session.add(request)
        db_session.commit()

        saved_item = db_session.query(ReturnItem).first()
        assert saved_item.return_request is not None
        assert saved_item.return_request.code == "RV-2026-0004"

    def test_return_request_cascade_delete(self, db_session):
        """Deleting a return request should cascade-delete its items."""
        request = ReturnRequest(
            code="RV-2026-0005",
            order_code="OBJ-700",
            customer_email="cascade@test.com",
            customer_name="Cascade Test",
        )
        request.items = [
            ReturnItem(
                product_code="P1",
                product_name="Product 1",
                unit_price=100.0,
                reason=ReturnReason.OTHER.value,
                refund_amount=100.0,
            ),
            ReturnItem(
                product_code="P2",
                product_name="Product 2",
                unit_price=200.0,
                reason=ReturnReason.ORDERED_WRONG.value,
                refund_amount=200.0,
            ),
        ]
        db_session.add(request)
        db_session.commit()

        assert db_session.query(ReturnItem).count() == 2

        db_session.delete(request)
        db_session.commit()

        assert db_session.query(ReturnRequest).count() == 0
        assert db_session.query(ReturnItem).count() == 0

    def test_return_request_unique_code(self, db_session):
        """Code must be unique across return requests."""
        r1 = ReturnRequest(
            code="RV-2026-0001",
            order_code="OBJ-1",
            customer_email="a@a.com",
            customer_name="A",
        )
        r2 = ReturnRequest(
            code="RV-2026-0001",
            order_code="OBJ-2",
            customer_email="b@b.com",
            customer_name="B",
        )
        db_session.add(r1)
        db_session.commit()

        db_session.add(r2)
        with pytest.raises(Exception):
            db_session.commit()
        db_session.rollback()


# --------------- Complaint tests ---------------


class TestComplaint:
    def test_create_complaint(self, db_session):
        complaint = Complaint(
            code="RE-2026-0001",
            order_code="OBJ-12345",
            customer_email="jan@example.com",
            customer_name="Jan Novak",
        )
        db_session.add(complaint)
        db_session.commit()

        saved = db_session.query(Complaint).first()
        assert saved is not None
        assert saved.code == "RE-2026-0001"
        assert saved.order_code == "OBJ-12345"

    def test_complaint_default_values(self, db_session):
        complaint = Complaint(
            code="RE-2026-0002",
            order_code="OBJ-99999",
            customer_email="test@test.com",
            customer_name="Test User",
        )
        db_session.add(complaint)
        db_session.commit()

        saved = db_session.query(Complaint).first()
        assert saved.eshop_id == "228312"
        assert saved.status == ComplaintStatus.NEW.value
        assert saved.photos_count == 0
        assert saved.preferred_resolution is None
        assert saved.photos_deleted_at is None

    def test_complaint_code_format(self, db_session):
        complaint = Complaint(
            code="RE-2026-0001",
            order_code="OBJ-100",
            customer_email="a@b.com",
            customer_name="A",
        )
        db_session.add(complaint)
        db_session.commit()

        saved = db_session.query(Complaint).first()
        assert saved.code.startswith("RE-")

    def test_complaint_with_items_and_photos(self, db_session):
        complaint = Complaint(
            code="RE-2026-0003",
            order_code="OBJ-500",
            customer_email="items@test.com",
            customer_name="Items Test",
            preferred_resolution=PreferredResolution.REFUND.value,
        )
        item = ComplaintItem(
            product_code="PROD-001",
            product_name="Whey Protein 1kg",
            quantity=1,
            unit_price=599.0,
            problem_description="Produkt byl plesnivej",
            doses_taken=3,
            discovery_date=date(2026, 3, 20),
            refund_amount=599.0,
        )
        photo = ComplaintPhoto(
            file_path="/uploads/complaints/re-2026-0003/photo1.jpg",
            original_filename="photo1.jpg",
        )
        complaint.items = [item]
        complaint.photos = [photo]
        db_session.add(complaint)
        db_session.commit()

        saved = db_session.query(Complaint).first()
        assert len(saved.items) == 1
        assert saved.items[0].problem_description == "Produkt byl plesnivej"
        assert saved.items[0].doses_taken == 3
        assert saved.items[0].discovery_date == date(2026, 3, 20)
        assert len(saved.photos) == 1
        assert saved.photos[0].original_filename == "photo1.jpg"

    def test_complaint_cascade_delete(self, db_session):
        """Deleting complaint cascades to items and photos."""
        complaint = Complaint(
            code="RE-2026-0004",
            order_code="OBJ-600",
            customer_email="cascade@test.com",
            customer_name="Cascade",
        )
        complaint.items = [
            ComplaintItem(
                product_code="P1",
                product_name="Product 1",
                unit_price=100.0,
                problem_description="Problem 1",
            )
        ]
        complaint.photos = [
            ComplaintPhoto(
                file_path="/uploads/photo1.jpg",
                original_filename="photo1.jpg",
            )
        ]
        db_session.add(complaint)
        db_session.commit()

        assert db_session.query(ComplaintItem).count() == 1
        assert db_session.query(ComplaintPhoto).count() == 1

        db_session.delete(complaint)
        db_session.commit()

        assert db_session.query(Complaint).count() == 0
        assert db_session.query(ComplaintItem).count() == 0
        assert db_session.query(ComplaintPhoto).count() == 0

    def test_complaint_unique_code(self, db_session):
        c1 = Complaint(
            code="RE-2026-0001",
            order_code="OBJ-1",
            customer_email="a@a.com",
            customer_name="A",
        )
        c2 = Complaint(
            code="RE-2026-0001",
            order_code="OBJ-2",
            customer_email="b@b.com",
            customer_name="B",
        )
        db_session.add(c1)
        db_session.commit()

        db_session.add(c2)
        with pytest.raises(Exception):
            db_session.commit()
        db_session.rollback()


# --------------- StatusHistory tests ---------------


class TestStatusHistory:
    def test_create_status_history(self, db_session):
        history = StatusHistory(
            entity_type="return",
            entity_id=1,
            old_status=None,
            new_status=ReturnStatus.NEW.value,
            changed_by="customer",
            note="Vytvorena nova zadost o vraceni",
        )
        db_session.add(history)
        db_session.commit()

        saved = db_session.query(StatusHistory).first()
        assert saved is not None
        assert saved.entity_type == "return"
        assert saved.entity_id == 1
        assert saved.old_status is None
        assert saved.new_status == "new"
        assert saved.changed_by == "customer"

    def test_status_history_for_complaint(self, db_session):
        history = StatusHistory(
            entity_type="complaint",
            entity_id=42,
            old_status=ComplaintStatus.NEW.value,
            new_status=ComplaintStatus.ASSESSING.value,
            changed_by="admin_user",
        )
        db_session.add(history)
        db_session.commit()

        saved = db_session.query(StatusHistory).first()
        assert saved.entity_type == "complaint"
        assert saved.old_status == "new"
        assert saved.new_status == "assessing"

    def test_multiple_history_entries(self, db_session):
        entries = [
            StatusHistory(
                entity_type="return",
                entity_id=1,
                new_status=ReturnStatus.NEW.value,
                changed_by="customer",
            ),
            StatusHistory(
                entity_type="return",
                entity_id=1,
                old_status=ReturnStatus.NEW.value,
                new_status=ReturnStatus.WAITING_FOR_DELIVERY.value,
                changed_by="admin",
            ),
            StatusHistory(
                entity_type="return",
                entity_id=1,
                old_status=ReturnStatus.WAITING_FOR_DELIVERY.value,
                new_status=ReturnStatus.RECEIVED_INSPECTING.value,
                changed_by="admin",
            ),
        ]
        db_session.add_all(entries)
        db_session.commit()

        count = db_session.query(StatusHistory).filter_by(
            entity_type="return", entity_id=1
        ).count()
        assert count == 3
