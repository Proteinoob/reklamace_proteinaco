import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin
from app.services import return_service, complaint_service
from app.models.return_request import ReturnRequest
from app.models.complaint import Complaint
from app.models.status_history import StatusHistory
from app.models.enums import ReturnStatus, ComplaintStatus
from app.schemas.return_schemas import ReturnDetailResponse, ReturnListResponse
from app.schemas.complaint_schemas import (
    ComplaintDetailResponse,
    ComplaintListResponse,
    AdminRequestInfoRequest,
    AdminApproveComplaintRequest,
    AdminRejectRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)


# --------------- Returns ---------------


@router.get("/returns", response_model=ReturnListResponse)
def list_returns(
    status: Optional[str] = Query(None),
    order_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all returns with optional filters."""
    return return_service.list_returns(
        db=db,
        status=status,
        order_code=order_code,
        page=page,
        page_size=page_size,
    )


@router.get("/returns/{return_id}", response_model=ReturnDetailResponse)
def get_return_detail(return_id: int, db: Session = Depends(get_db)):
    """Get full detail of a return request."""
    try:
        return return_service.get_return_detail(return_id, db)
    except ValueError:
        raise HTTPException(status_code=404, detail="Return not found")


@router.post("/returns/{return_id}/receive")
def receive_return(
    return_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Mark a return as received and begin inspection."""
    try:
        result = return_service.receive_return(return_id, db, admin["username"])
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/returns/{return_id}/approve")
async def approve_return(
    return_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Approve a return and create credit note."""
    try:
        result = await return_service.approve_return(
            return_id, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/returns/{return_id}/reject")
def reject_return(
    return_id: int,
    body: AdminRejectRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Reject a return with a reason."""
    try:
        result = return_service.reject_return(
            return_id, body.reason, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/returns/{return_id}/mark-refunded")
def mark_refunded(
    return_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Mark a return as completed (refund sent)."""
    try:
        result = return_service.mark_refunded(return_id, db, admin["username"])
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------- Complaints ---------------


@router.get("/complaints", response_model=ComplaintListResponse)
def list_complaints(
    status: Optional[str] = Query(None),
    order_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all complaints with optional filters."""
    return complaint_service.list_complaints(
        db=db,
        status=status,
        order_code=order_code,
        page=page,
        page_size=page_size,
    )


@router.get("/complaints/{complaint_id}", response_model=ComplaintDetailResponse)
def get_complaint_detail(complaint_id: int, db: Session = Depends(get_db)):
    """Get full detail of a complaint."""
    try:
        return complaint_service.get_complaint_detail(complaint_id, db)
    except ValueError:
        raise HTTPException(status_code=404, detail="Complaint not found")


@router.post("/complaints/{complaint_id}/request-info")
def request_info(
    complaint_id: int,
    body: AdminRequestInfoRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Request additional information from the customer."""
    try:
        result = complaint_service.request_more_info(
            complaint_id, body.message, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/complaints/{complaint_id}/start-assessment")
def start_assessment(
    complaint_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Start assessing a complaint."""
    try:
        result = complaint_service.start_assessment(
            complaint_id, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/complaints/{complaint_id}/approve")
async def approve_complaint(
    complaint_id: int,
    body: AdminApproveComplaintRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Approve a complaint with resolution type."""
    try:
        result = await complaint_service.approve_complaint(
            complaint_id=complaint_id,
            resolution=body.resolution,
            note=body.note,
            db=db,
            admin_user=admin["username"],
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/complaints/{complaint_id}/reject")
def reject_complaint(
    complaint_id: int,
    body: AdminRejectRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Reject a complaint with a reason."""
    try:
        result = complaint_service.reject_complaint(
            complaint_id, body.reason, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/complaints/{complaint_id}/resolve")
async def resolve_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Resolve an approved complaint."""
    try:
        result = await complaint_service.resolve_complaint(
            complaint_id, db, admin["username"]
        )
        return {"status": "ok", "new_status": result.status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------- Dashboard ---------------


# Statuses that require admin action
_RETURN_ACTION_STATUSES = {
    ReturnStatus.NEW.value,
    ReturnStatus.RECEIVED_INSPECTING.value,
    ReturnStatus.REFUND_READY.value,
}

_COMPLAINT_ACTION_STATUSES = {
    ComplaintStatus.NEW.value,
    ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
    ComplaintStatus.ASSESSING.value,
}

SLA_DAYS = 7


@router.get("/dashboard")
def get_dashboard(db: Session = Depends(get_db)):
    """Aggregate dashboard with counts per status."""
    # Return counts by status
    return_counts = _count_by_status(db, ReturnRequest, ReturnStatus)
    complaint_counts = _count_by_status(db, Complaint, ComplaintStatus)

    # Action required: items needing admin attention
    action_returns = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.status.in_(_RETURN_ACTION_STATUSES))
        .count()
    )
    action_complaints = (
        db.query(Complaint)
        .filter(Complaint.status.in_(_COMPLAINT_ACTION_STATUSES))
        .count()
    )
    action_required = action_returns + action_complaints

    # SLA breached: items in current status > SLA_DAYS days
    sla_breached = _count_sla_breached(db)

    return {
        "returns": return_counts,
        "complaints": complaint_counts,
        "action_required": action_required,
        "sla_breached": sla_breached,
    }


def _count_by_status(db: Session, model, status_enum) -> dict:
    """Count records per status for a given model."""
    counts = {}
    total = 0
    for s in status_enum:
        count = db.query(model).filter(model.status == s.value).count()
        counts[s.value] = count
        total += count
    counts["total"] = total
    return counts


def _count_sla_breached(db: Session) -> int:
    """Count items that have been in their current status for more than SLA_DAYS."""
    now = datetime.now(timezone.utc)
    breached = 0

    # Check returns via StatusHistory
    active_returns = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.status.notin_([
            ReturnStatus.COMPLETED.value,
            ReturnStatus.REJECTED.value,
        ]))
        .all()
    )
    for ret in active_returns:
        last_change = (
            db.query(StatusHistory)
            .filter(
                StatusHistory.entity_type == "return",
                StatusHistory.entity_id == ret.id,
            )
            .order_by(StatusHistory.created_at.desc())
            .first()
        )
        if last_change and last_change.created_at:
            change_time = last_change.created_at
            if change_time.tzinfo is None:
                change_time = change_time.replace(tzinfo=timezone.utc)
            if (now - change_time).days > SLA_DAYS:
                breached += 1

    # Check complaints via updated_at/created_at
    active_complaints = (
        db.query(Complaint)
        .filter(Complaint.status.notin_([
            ComplaintStatus.RESOLVED.value,
            ComplaintStatus.REJECTED.value,
        ]))
        .all()
    )
    for comp in active_complaints:
        ref = comp.updated_at or comp.created_at
        if ref:
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            if (now - ref).days > SLA_DAYS:
                breached += 1

    return breached
