"""Tests for admin API endpoints."""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

from tests.conftest import create_test_admin_token
from app.models.enums import ReturnStatus, ComplaintStatus, PreferredResolution
from app.schemas.return_schemas import (
    ReturnListResponse,
    ReturnListItem,
    ReturnDetailResponse,
    ReturnItemDetail,
)
from app.schemas.complaint_schemas import (
    ComplaintListResponse,
    ComplaintListItem,
    ComplaintDetailResponse,
    ComplaintItemDetail,
    ComplaintPhotoDetail,
)
from app.schemas.common import StatusHistoryEntry


# --------------- Helpers ---------------

NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)


def _make_return_list_response():
    return ReturnListResponse(
        items=[
            ReturnListItem(
                id=1,
                code="RV-2026-0001",
                order_code="OBJ-001",
                customer_name="Jan Novak",
                customer_email="jan@test.cz",
                status=ReturnStatus.NEW.value,
                total_refund_amount=500.0,
                days_in_current_status=0,
                created_at=NOW,
            )
        ],
        total=1,
        page=1,
        page_size=20,
    )


def _make_return_detail_response():
    return ReturnDetailResponse(
        id=1,
        code="RV-2026-0001",
        order_code="OBJ-001",
        customer_email="jan@test.cz",
        customer_name="Jan Novak",
        status=ReturnStatus.WAITING_FOR_DELIVERY.value,
        total_refund_amount=500.0,
        items=[
            ReturnItemDetail(
                id=1,
                product_code="PROD-1",
                product_name="Protein",
                quantity=1,
                unit_price=500.0,
                reason="ordered_wrong",
                refund_amount=500.0,
            )
        ],
        status_history=[
            StatusHistoryEntry(
                old_status=None,
                new_status=ReturnStatus.NEW.value,
                changed_by="customer",
                note="Created",
                created_at=NOW,
            )
        ],
        days_in_current_status=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_return_request_mock(status="received_inspecting"):
    """Create a mock ReturnRequest object."""
    mock = MagicMock()
    mock.id = 1
    mock.code = "RV-2026-0001"
    mock.status = status
    return mock


def _make_complaint_list_response():
    return ComplaintListResponse(
        items=[
            ComplaintListItem(
                id=1,
                code="RE-2026-0001",
                order_code="OBJ-001",
                customer_name="Jan Novak",
                customer_email="jan@test.cz",
                status=ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
                preferred_resolution="refund",
                photos_count=0,
                days_in_current_status=0,
                created_at=NOW,
            )
        ],
        total=1,
        page=1,
        page_size=20,
    )


def _make_complaint_detail_response():
    return ComplaintDetailResponse(
        id=1,
        code="RE-2026-0001",
        order_code="OBJ-001",
        customer_email="jan@test.cz",
        customer_name="Jan Novak",
        status=ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
        preferred_resolution="refund",
        admin_note=None,
        photos_count=0,
        items=[
            ComplaintItemDetail(
                id=1,
                product_code="PROD-1",
                product_name="Protein",
                quantity=1,
                unit_price=500.0,
                problem_description="Bad taste",
            )
        ],
        photos=[],
        status_history=[
            StatusHistoryEntry(
                old_status=None,
                new_status=ComplaintStatus.NEW.value,
                changed_by="customer",
                note="Created",
                created_at=NOW,
            )
        ],
        days_in_current_status=0,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_complaint_mock(status="assessing"):
    mock = MagicMock()
    mock.id = 1
    mock.code = "RE-2026-0001"
    mock.status = status
    return mock


# --------------- Returns tests ---------------


class TestListReturns:
    @patch("app.api.admin.return_service.list_returns")
    def test_list_returns(self, mock_list, admin_client):
        mock_list.return_value = _make_return_list_response()
        resp = admin_client.get("/api/v1/admin/returns")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["code"] == "RV-2026-0001"
        mock_list.assert_called_once()


class TestGetReturnDetail:
    @patch("app.api.admin.return_service.get_return_detail")
    def test_get_return_detail(self, mock_detail, admin_client):
        mock_detail.return_value = _make_return_detail_response()
        resp = admin_client.get("/api/v1/admin/returns/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "RV-2026-0001"
        assert len(data["items"]) == 1

    @patch("app.api.admin.return_service.get_return_detail")
    def test_get_return_detail_not_found(self, mock_detail, admin_client):
        mock_detail.side_effect = ValueError("Not found")
        resp = admin_client.get("/api/v1/admin/returns/999")
        assert resp.status_code == 404


class TestReceiveReturn:
    @patch("app.api.admin.return_service.receive_return")
    def test_receive_return(self, mock_receive, admin_client):
        mock_receive.return_value = _make_return_request_mock("received_inspecting")
        resp = admin_client.post("/api/v1/admin/returns/1/receive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "received_inspecting"

    @patch("app.api.admin.return_service.receive_return")
    def test_receive_return_bad_transition(self, mock_receive, admin_client):
        mock_receive.side_effect = ValueError("Invalid transition")
        resp = admin_client.post("/api/v1/admin/returns/1/receive")
        assert resp.status_code == 400


class TestApproveReturn:
    @patch("app.api.admin.return_service.approve_return", new_callable=AsyncMock)
    def test_approve_return(self, mock_approve, admin_client):
        mock_approve.return_value = _make_return_request_mock("refund_ready")
        resp = admin_client.post("/api/v1/admin/returns/1/approve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "refund_ready"

    @patch("app.api.admin.return_service.approve_return", new_callable=AsyncMock)
    def test_approve_return_bad_transition(self, mock_approve, admin_client):
        mock_approve.side_effect = ValueError("Invalid transition")
        resp = admin_client.post("/api/v1/admin/returns/1/approve")
        assert resp.status_code == 400


class TestRejectReturn:
    @patch("app.api.admin.return_service.reject_return")
    def test_reject_return(self, mock_reject, admin_client):
        mock_reject.return_value = _make_return_request_mock("rejected")
        resp = admin_client.post(
            "/api/v1/admin/returns/1/reject",
            json={"reason": "Damaged by customer"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "rejected"

    def test_reject_return_missing_reason(self, admin_client):
        """Empty reason should fail Pydantic validation (422)."""
        resp = admin_client.post(
            "/api/v1/admin/returns/1/reject",
            json={"reason": "  "},
        )
        assert resp.status_code == 422


class TestMarkRefunded:
    @patch("app.api.admin.return_service.mark_refunded")
    def test_mark_refunded(self, mock_refund, admin_client):
        mock_refund.return_value = _make_return_request_mock("completed")
        resp = admin_client.post("/api/v1/admin/returns/1/mark-refunded")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "completed"


# --------------- Complaints tests ---------------


class TestListComplaints:
    @patch("app.api.admin.complaint_service.list_complaints")
    def test_list_complaints(self, mock_list, admin_client):
        mock_list.return_value = _make_complaint_list_response()
        resp = admin_client.get("/api/v1/admin/complaints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["code"] == "RE-2026-0001"


class TestGetComplaintDetail:
    @patch("app.api.admin.complaint_service.get_complaint_detail")
    def test_get_complaint_detail(self, mock_detail, admin_client):
        mock_detail.return_value = _make_complaint_detail_response()
        resp = admin_client.get("/api/v1/admin/complaints/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == "RE-2026-0001"

    @patch("app.api.admin.complaint_service.get_complaint_detail")
    def test_get_complaint_detail_not_found(self, mock_detail, admin_client):
        mock_detail.side_effect = ValueError("Not found")
        resp = admin_client.get("/api/v1/admin/complaints/999")
        assert resp.status_code == 404


class TestRequestInfo:
    @patch("app.api.admin.complaint_service.request_more_info")
    def test_request_info(self, mock_req, admin_client):
        mock_req.return_value = _make_complaint_mock("need_more_info")
        resp = admin_client.post(
            "/api/v1/admin/complaints/1/request-info",
            json={"message": "Please send photos"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "need_more_info"


class TestStartAssessment:
    @patch("app.api.admin.complaint_service.start_assessment")
    def test_start_assessment(self, mock_start, admin_client):
        mock_start.return_value = _make_complaint_mock("assessing")
        resp = admin_client.post("/api/v1/admin/complaints/1/start-assessment")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "assessing"


class TestApproveComplaint:
    @patch("app.api.admin.complaint_service.approve_complaint", new_callable=AsyncMock)
    def test_approve_complaint(self, mock_approve, admin_client):
        mock_approve.return_value = _make_complaint_mock("approved")
        resp = admin_client.post(
            "/api/v1/admin/complaints/1/approve",
            json={"resolution": "refund", "note": "Product was indeed defective"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "approved"

    @patch("app.api.admin.complaint_service.approve_complaint", new_callable=AsyncMock)
    def test_approve_complaint_bad_transition(self, mock_approve, admin_client):
        mock_approve.side_effect = ValueError("Invalid transition")
        resp = admin_client.post(
            "/api/v1/admin/complaints/1/approve",
            json={"resolution": "refund"},
        )
        assert resp.status_code == 400


class TestRejectComplaint:
    @patch("app.api.admin.complaint_service.reject_complaint")
    def test_reject_complaint(self, mock_reject, admin_client):
        mock_reject.return_value = _make_complaint_mock("rejected")
        resp = admin_client.post(
            "/api/v1/admin/complaints/1/reject",
            json={"reason": "No defect found"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "rejected"


class TestResolveComplaint:
    @patch("app.api.admin.complaint_service.resolve_complaint", new_callable=AsyncMock)
    def test_resolve_complaint(self, mock_resolve, admin_client):
        mock_resolve.return_value = _make_complaint_mock("resolved")
        resp = admin_client.post("/api/v1/admin/complaints/1/resolve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "resolved"


# --------------- Dashboard tests ---------------


class TestDashboard:
    def test_dashboard(self, admin_client, db_session):
        """Dashboard returns correct structure with empty DB."""
        resp = admin_client.get("/api/v1/admin/dashboard")
        assert resp.status_code == 200
        data = resp.json()

        # Check structure
        assert "returns" in data
        assert "complaints" in data
        assert "action_required" in data
        assert "sla_breached" in data

        # Check returns sub-dict has all statuses
        for s in ReturnStatus:
            assert s.value in data["returns"]
        assert "total" in data["returns"]

        # Check complaints sub-dict has all statuses
        for s in ComplaintStatus:
            assert s.value in data["complaints"]
        assert "total" in data["complaints"]

        # Empty DB = all zeros
        assert data["returns"]["total"] == 0
        assert data["complaints"]["total"] == 0
        assert data["action_required"] == 0
        assert data["sla_breached"] == 0


# --------------- Auth tests ---------------


class TestAuth:
    def test_unauthorized_no_token(self, client):
        """Request without Authorization header should return 401."""
        resp = client.get("/api/v1/admin/returns")
        assert resp.status_code == 401
        assert "Authorization header missing" in resp.json()["detail"]

    def test_unauthorized_invalid_token(self, client):
        """Request with invalid token should return 401."""
        resp = client.get(
            "/api/v1/admin/returns",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401
        assert "Invalid or expired token" in resp.json()["detail"]

    def test_forbidden_wrong_app(self, client):
        """Token without 'reklamace' in allowed_apps should return 403."""
        token = create_test_admin_token(allowed_apps=["admin"])
        resp = client.get(
            "/api/v1/admin/returns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert "Access to reklamace not permitted" in resp.json()["detail"]
