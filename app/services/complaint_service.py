import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.complaint import Complaint, ComplaintItem, ComplaintPhoto
from app.models.status_history import StatusHistory
from app.models.enums import ComplaintStatus, PreferredResolution
from app.schemas.complaint_schemas import (
    ComplaintCreateRequest, ComplaintCreateResponse, ComplaintDetailResponse,
    ComplaintItemDetail, ComplaintPhotoDetail, ComplaintListItem,
    ComplaintListResponse, ComplaintTrackingResponse,
    ComplaintSupplementRequest, AdminRequestInfoRequest,
    AdminApproveComplaintRequest, AdminRejectRequest,
)
from app.schemas.common import (
    OrderLookupRequest, OrderLookupResponse, OrderProductItem,
    StatusHistoryEntry,
)
from app.services.shoptet_client import ShoptetClient
from app.services.zasilkovna import ZasilkovnaClient, ZasilkovnaError
from app.services.photo_service import save_photo, PhotoValidationError
from app.core.email import EmailService

logger = logging.getLogger(__name__)

# Valid status transitions
COMPLAINT_TRANSITIONS = {
    ComplaintStatus.NEW: [ComplaintStatus.WAITING_FOR_ASSESSMENT],
    ComplaintStatus.WAITING_FOR_ASSESSMENT: [
        ComplaintStatus.NEED_MORE_INFO,
        ComplaintStatus.ASSESSING,
    ],
    ComplaintStatus.NEED_MORE_INFO: [ComplaintStatus.WAITING_FOR_ASSESSMENT],
    ComplaintStatus.ASSESSING: [ComplaintStatus.APPROVED, ComplaintStatus.REJECTED],
    ComplaintStatus.APPROVED: [ComplaintStatus.RESOLVED],
    ComplaintStatus.REJECTED: [],
    ComplaintStatus.RESOLVED: [],
}

STATUS_LABELS = {
    ComplaintStatus.NEW: "Nova",
    ComplaintStatus.WAITING_FOR_ASSESSMENT: "Ceka na posouzeni",
    ComplaintStatus.NEED_MORE_INFO: "Ceka na doplneni informaci",
    ComplaintStatus.ASSESSING: "Posuzuje se",
    ComplaintStatus.APPROVED: "Schvalena",
    ComplaintStatus.REJECTED: "Zamitnuta",
    ComplaintStatus.RESOLVED: "Vyresena",
}


# --------------- Helpers ---------------


def _validate_transition(
    current_status: ComplaintStatus,
    new_status: ComplaintStatus,
) -> None:
    """Raise ValueError when the transition is not allowed."""
    allowed = COMPLAINT_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise ValueError(
            f"Invalid status transition: {current_status.value} -> {new_status.value}"
        )


def _log_status_change(
    db: Session,
    complaint: Complaint,
    old_status: str | None,
    new_status: str,
    changed_by: str,
    note: str | None = None,
) -> StatusHistory:
    """Create a StatusHistory record for a complaint."""
    entry = StatusHistory(
        entity_type="complaint",
        entity_id=complaint.id,
        old_status=old_status,
        new_status=new_status,
        changed_by=changed_by,
        note=note,
    )
    db.add(entry)
    return entry


def _calculate_days_in_status(complaint: Complaint) -> int:
    """Return how many full days the complaint has been in its current status."""
    reference = complaint.updated_at or complaint.created_at
    if reference is None:
        return 0
    now = datetime.now(timezone.utc)
    # Make reference offset-aware if it is naive
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    delta = now - reference
    return max(delta.days, 0)


# --------------- Code generation ---------------


def generate_complaint_code(db: Session) -> str:
    """Generate sequential complaint code: RE-{year}-NNNN."""
    year = datetime.now(timezone.utc).year
    prefix = f"RE-{year}-"

    last = (
        db.query(Complaint)
        .filter(Complaint.code.like(f"{prefix}%"))
        .order_by(Complaint.id.desc())
        .first()
    )

    if last:
        last_num = int(last.code.split("-")[-1])
        next_num = last_num + 1
    else:
        next_num = 1

    return f"{prefix}{next_num:04d}"


# --------------- Create complaint ---------------


_ACTIVE_COMPLAINT_STATUSES = {
    ComplaintStatus.NEW.value,
    ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
    ComplaintStatus.NEED_MORE_INFO.value,
    ComplaintStatus.ASSESSING.value,
    ComplaintStatus.APPROVED.value,
}


def check_existing_complaint(order_code: str, email: str, db: Session) -> Complaint | None:
    """Check if an active complaint already exists for this order + email."""
    return (
        db.query(Complaint)
        .filter(
            Complaint.order_code == order_code,
            Complaint.customer_email == email.lower().strip(),
            Complaint.status.in_(_ACTIVE_COMPLAINT_STATUSES),
        )
        .first()
    )


async def create_complaint(
    request: ComplaintCreateRequest,
    db: Session,
    shoptet_client: ShoptetClient | None = None,
    zasilkovna_client: ZasilkovnaClient | None = None,
    email_service: EmailService | None = None,
) -> ComplaintCreateResponse:
    """Create a new complaint with items, Zasilkovna label, and confirmation email."""
    # 0. Check for existing active complaint on this order
    existing = check_existing_complaint(request.order_code, request.email, db)
    if existing:
        raise ValueError(
            f"EXISTING:{existing.code}|Pro tuto objednávku již existuje aktivní reklamace {existing.code}."
        )

    # 1. Lookup order via Shoptet
    client = shoptet_client or ShoptetClient()
    order_data = await client.get_order(request.order_code)
    order = order_data.get("order", order_data)

    # Build product lookup for price/name
    order_items_map = {}
    for item in order.get("items", []):
        order_items_map[item["code"]] = item

    # 2. Generate code
    code = generate_complaint_code(db)

    # 3. Create complaint record
    complaint = Complaint(
        code=code,
        order_code=request.order_code,
        customer_email=request.email,
        customer_name=request.name,
        customer_phone=request.phone,
        bank_account=request.bank_account,
        status=ComplaintStatus.NEW.value,
        preferred_resolution=request.items[0].preferred_resolution.value,
    )
    db.add(complaint)
    db.flush()  # get complaint.id

    # 3b. Create complaint items
    items_for_email = []
    total_value = 0.0
    for req_item in request.items:
        order_item = order_items_map.get(req_item.product_code, {})
        unit_price = order_item.get("itemPrice", {}).get("withVat", 0.0)
        product_name = order_item.get("name", req_item.product_code)

        ci = ComplaintItem(
            complaint_id=complaint.id,
            product_code=req_item.product_code,
            product_name=product_name,
            quantity=req_item.quantity,
            unit_price=unit_price,
            problem_description=req_item.problem_description,
            doses_taken=req_item.doses_taken,
            discovery_date=(
                datetime.strptime(req_item.discovery_date, "%Y-%m-%d").date()
                if req_item.discovery_date
                else None
            ),
        )
        db.add(ci)
        total_value += unit_price * req_item.quantity
        items_for_email.append(
            {"product_name": product_name, "quantity": req_item.quantity}
        )

    # 4. Log NEW status
    _log_status_change(
        db, complaint, None, ComplaintStatus.NEW.value, "customer",
        note="Reklamace vytvorena zakaznikem",
    )

    # Auto-transition to WAITING_FOR_ASSESSMENT
    old_status = complaint.status
    complaint.status = ComplaintStatus.WAITING_FOR_ASSESSMENT.value
    complaint.updated_at = datetime.now(timezone.utc)
    _log_status_change(
        db, complaint, old_status, ComplaintStatus.WAITING_FOR_ASSESSMENT.value,
        "system", note="Automaticky prechod po vytvoreni",
    )

    # 5. Create Zasilkovna return packet
    label_url = None
    zas = zasilkovna_client or ZasilkovnaClient()
    try:
        name_parts = request.name.split(" ", 1)
        first_name = name_parts[0]
        surname = name_parts[1] if len(name_parts) > 1 else ""

        packet = await zas.create_return_packet(
            case_code=code,
            customer_name=first_name,
            customer_surname=surname,
            customer_email=request.email,
            customer_phone=request.phone or "",
            value=total_value,
        )
        # 6. Save label URL
        packet_id = packet.get("packet_id")
        if packet_id:
            label_url = (
                f"https://www.zasilkovna.cz/api/packetLabelPdf"
                f"?packetId={packet_id}"
            )
            complaint.shipping_label_url = label_url
            complaint.tracking_number = packet.get("barcode")
    except ZasilkovnaError as exc:
        logger.error("Zasilkovna packet creation failed: %s", exc)

    db.commit()
    db.refresh(complaint)

    # 7. Send confirmation email
    email_svc = email_service or EmailService()
    try:
        await email_svc.send_complaint_confirmation(
            to=request.email,
            complaint_code=code,
            items=items_for_email,
            label_url=label_url,
        )
    except Exception as exc:
        logger.error("Failed to send confirmation email: %s", exc)

    # 9. Return response
    return ComplaintCreateResponse(
        code=code,
        instructions=(
            "Vasi reklamaci jsme prijali. "
            "Zaslete prosim zbozi na nasi adresu pomoci prilozeneho stitku."
        ),
        label_url=label_url,
    )


# --------------- Upload photos ---------------


async def upload_photos(
    complaint_code: str,
    email: str,
    files: list[tuple[bytes, str, str]],
    db: Session,
) -> list[ComplaintPhoto]:
    """Upload photos for a complaint. files = list of (data, filename, content_type)."""
    complaint = (
        db.query(Complaint).filter(Complaint.code == complaint_code).first()
    )
    if complaint is None:
        raise ValueError(f"Complaint {complaint_code} not found")

    if complaint.customer_email.lower() != email.lower():
        raise ValueError("Email does not match complaint owner")

    photos = []
    for file_data, filename, content_type in files:
        photo = save_photo(
            complaint_id=complaint.id,
            file_data=file_data,
            original_filename=filename,
            content_type=content_type,
            db=db,
        )
        photos.append(photo)

    return photos


# --------------- Supplement complaint ---------------


async def supplement_complaint(
    complaint_code: str,
    email: str,
    message: str,
    db: Session,
    email_service: EmailService | None = None,
) -> Complaint:
    """Customer supplements a complaint with additional info."""
    complaint = (
        db.query(Complaint).filter(Complaint.code == complaint_code).first()
    )
    if complaint is None:
        raise ValueError(f"Complaint {complaint_code} not found")

    if complaint.customer_email.lower() != email.lower():
        raise ValueError("Email does not match complaint owner")

    current = ComplaintStatus(complaint.status)
    if current != ComplaintStatus.NEED_MORE_INFO:
        raise ValueError(
            f"Cannot supplement complaint in status {current.value}. "
            f"Expected: {ComplaintStatus.NEED_MORE_INFO.value}"
        )

    # Transition back to WAITING_FOR_ASSESSMENT
    old_status = complaint.status
    new_status = ComplaintStatus.WAITING_FOR_ASSESSMENT
    _validate_transition(current, new_status)

    complaint.status = new_status.value
    complaint.updated_at = datetime.now(timezone.utc)

    _log_status_change(
        db, complaint, old_status, new_status.value,
        "customer", note=message,
    )

    db.commit()
    db.refresh(complaint)

    # Send status change email
    email_svc = email_service or EmailService()
    try:
        await email_svc.send_status_change(
            to=complaint.customer_email,
            case_code=complaint.code,
            case_type="complaint",
            new_status=new_status.value,
            status_label=STATUS_LABELS[new_status],
        )
    except Exception as exc:
        logger.error("Failed to send status change email: %s", exc)

    return complaint


# --------------- Admin: request more info ---------------


def request_more_info(
    complaint_id: int,
    message: str,
    db: Session,
    admin_user: str,
    email_service: EmailService | None = None,
) -> Complaint:
    """Admin requests additional information from the customer."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    current = ComplaintStatus(complaint.status)
    new_status = ComplaintStatus.NEED_MORE_INFO
    _validate_transition(current, new_status)

    old_status = complaint.status
    complaint.status = new_status.value
    complaint.updated_at = datetime.now(timezone.utc)

    _log_status_change(
        db, complaint, old_status, new_status.value,
        admin_user, note=message,
    )

    db.commit()
    db.refresh(complaint)

    return complaint


# --------------- Admin: start assessment ---------------


def start_assessment(
    complaint_id: int,
    db: Session,
    admin_user: str,
    email_service: EmailService | None = None,
) -> Complaint:
    """Admin transitions complaint from WAITING_FOR_ASSESSMENT to ASSESSING."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    current = ComplaintStatus(complaint.status)
    new_status = ComplaintStatus.ASSESSING
    _validate_transition(current, new_status)

    old_status = complaint.status
    complaint.status = new_status.value
    complaint.updated_at = datetime.now(timezone.utc)

    _log_status_change(
        db, complaint, old_status, new_status.value,
        admin_user, note="Zahajeno posouzeni",
    )

    db.commit()
    db.refresh(complaint)

    # Send status change email
    if email_service:
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                email_service.send_status_change(
                    to=complaint.customer_email,
                    case_code=complaint.code,
                    case_type="complaint",
                    new_status=new_status.value,
                    status_label=STATUS_LABELS[new_status],
                )
            )
        except Exception as exc:
            logger.error("Failed to send status change email: %s", exc)

    return complaint


# --------------- Admin: approve complaint ---------------


async def approve_complaint(
    complaint_id: int,
    resolution: PreferredResolution,
    note: str | None,
    db: Session,
    admin_user: str,
    shoptet_client: ShoptetClient | None = None,
    email_service: EmailService | None = None,
) -> Complaint:
    """Admin approves a complaint. Creates credit note for REFUND resolution."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    current = ComplaintStatus(complaint.status)
    new_status = ComplaintStatus.APPROVED
    _validate_transition(current, new_status)

    old_status = complaint.status
    complaint.status = new_status.value
    complaint.preferred_resolution = resolution.value
    complaint.admin_note = note
    complaint.updated_at = datetime.now(timezone.utc)

    # If resolution is REFUND, create credit note via Shoptet
    if resolution == PreferredResolution.REFUND:
        client = shoptet_client or ShoptetClient()
        try:
            order_data = await client.get_order(complaint.order_code)
            order = order_data.get("order", order_data)
            invoices = order.get("invoices", [])
            if invoices:
                invoice_code = invoices[0].get("code")
                if invoice_code:
                    await client.create_credit_note(invoice_code)
                    logger.info(
                        "Credit note created for complaint %s, invoice %s",
                        complaint.code, invoice_code,
                    )
        except Exception as exc:
            logger.error("Failed to create credit note: %s", exc)

    history_note = f"Schvaleno - reseni: {resolution.value}"
    if note:
        history_note += f". {note}"

    _log_status_change(
        db, complaint, old_status, new_status.value,
        admin_user, note=history_note,
    )

    db.commit()
    db.refresh(complaint)

    # Send email
    email_svc = email_service or EmailService()
    try:
        await email_svc.send_status_change(
            to=complaint.customer_email,
            case_code=complaint.code,
            case_type="complaint",
            new_status=new_status.value,
            status_label=STATUS_LABELS[new_status],
        )
    except Exception as exc:
        logger.error("Failed to send approval email: %s", exc)

    return complaint


# --------------- Admin: reject complaint ---------------


def reject_complaint(
    complaint_id: int,
    reason: str,
    db: Session,
    admin_user: str,
    email_service: EmailService | None = None,
) -> Complaint:
    """Admin rejects a complaint."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    current = ComplaintStatus(complaint.status)
    new_status = ComplaintStatus.REJECTED
    _validate_transition(current, new_status)

    old_status = complaint.status
    complaint.status = new_status.value
    complaint.admin_note = reason
    complaint.updated_at = datetime.now(timezone.utc)

    _log_status_change(
        db, complaint, old_status, new_status.value,
        admin_user, note=f"Zamitnuto: {reason}",
    )

    db.commit()
    db.refresh(complaint)

    return complaint


# --------------- Admin: resolve complaint ---------------


async def resolve_complaint(
    complaint_id: int,
    db: Session,
    admin_user: str,
    email_service: EmailService | None = None,
) -> Complaint:
    """Admin resolves an approved complaint."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    current = ComplaintStatus(complaint.status)
    new_status = ComplaintStatus.RESOLVED
    _validate_transition(current, new_status)

    old_status = complaint.status
    complaint.status = new_status.value
    complaint.updated_at = datetime.now(timezone.utc)

    _log_status_change(
        db, complaint, old_status, new_status.value,
        admin_user, note="Reklamace vyresena",
    )

    db.commit()
    db.refresh(complaint)

    # Send resolution email
    email_svc = email_service or EmailService()
    try:
        await email_svc.send_resolution(
            to=complaint.customer_email,
            case_code=complaint.code,
            case_type="complaint",
            resolution_type=complaint.preferred_resolution or "other",
            details="Vase reklamace byla uspesne vyresena.",
        )
    except Exception as exc:
        logger.error("Failed to send resolution email: %s", exc)

    return complaint


# --------------- Read operations ---------------


def get_complaint_detail(complaint_id: int, db: Session) -> ComplaintDetailResponse:
    """Full detail view of a complaint for admin."""
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint is None:
        raise ValueError(f"Complaint id={complaint_id} not found")

    items = [
        ComplaintItemDetail(
            id=item.id,
            product_code=item.product_code,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            problem_description=item.problem_description,
            doses_taken=item.doses_taken,
            discovery_date=(
                item.discovery_date.isoformat() if item.discovery_date else None
            ),
            refund_amount=item.refund_amount,
        )
        for item in complaint.items
    ]

    photos = [
        ComplaintPhotoDetail(
            id=photo.id,
            original_filename=photo.original_filename,
            uploaded_at=photo.uploaded_at or datetime.now(timezone.utc),
        )
        for photo in complaint.photos
    ]

    history_records = (
        db.query(StatusHistory)
        .filter(
            StatusHistory.entity_type == "complaint",
            StatusHistory.entity_id == complaint.id,
        )
        .order_by(StatusHistory.created_at.asc())
        .all()
    )

    status_history = [
        StatusHistoryEntry(
            old_status=h.old_status,
            new_status=h.new_status,
            changed_by=h.changed_by,
            note=h.note,
            created_at=h.created_at or datetime.now(timezone.utc),
        )
        for h in history_records
    ]

    return ComplaintDetailResponse(
        id=complaint.id,
        code=complaint.code,
        order_code=complaint.order_code,
        customer_email=complaint.customer_email,
        customer_name=complaint.customer_name,
        customer_phone=complaint.customer_phone,
        bank_account=complaint.bank_account,
        status=complaint.status,
        preferred_resolution=complaint.preferred_resolution,
        shipping_label_url=complaint.shipping_label_url,
        tracking_number=complaint.tracking_number,
        admin_note=complaint.admin_note,
        photos_count=complaint.photos_count or 0,
        items=items,
        photos=photos,
        status_history=status_history,
        days_in_current_status=_calculate_days_in_status(complaint),
        created_at=complaint.created_at or datetime.now(timezone.utc),
        updated_at=complaint.updated_at or datetime.now(timezone.utc),
    )


def get_complaint_by_code(
    code: str,
    email: str,
    db: Session,
) -> ComplaintTrackingResponse:
    """Customer tracking: look up complaint by code and verify email."""
    complaint = db.query(Complaint).filter(Complaint.code == code).first()
    if complaint is None:
        raise ValueError(f"Complaint {code} not found")

    if complaint.customer_email.lower() != email.lower():
        raise ValueError("Email does not match complaint owner")

    items = [
        ComplaintItemDetail(
            id=item.id,
            product_code=item.product_code,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            problem_description=item.problem_description,
            doses_taken=item.doses_taken,
            discovery_date=(
                item.discovery_date.isoformat() if item.discovery_date else None
            ),
            refund_amount=item.refund_amount,
        )
        for item in complaint.items
    ]

    photos = [
        ComplaintPhotoDetail(
            id=photo.id,
            original_filename=photo.original_filename,
            uploaded_at=photo.uploaded_at or datetime.now(timezone.utc),
        )
        for photo in complaint.photos
    ]

    history_records = (
        db.query(StatusHistory)
        .filter(
            StatusHistory.entity_type == "complaint",
            StatusHistory.entity_id == complaint.id,
        )
        .order_by(StatusHistory.created_at.asc())
        .all()
    )

    status_history = [
        StatusHistoryEntry(
            old_status=h.old_status,
            new_status=h.new_status,
            changed_by=h.changed_by,
            note=h.note,
            created_at=h.created_at or datetime.now(timezone.utc),
        )
        for h in history_records
    ]

    status_enum = ComplaintStatus(complaint.status)
    status_label = STATUS_LABELS.get(status_enum, complaint.status)

    return ComplaintTrackingResponse(
        code=complaint.code,
        status=complaint.status,
        status_label=status_label,
        items=items,
        photos=photos,
        status_history=status_history,
        created_at=complaint.created_at or datetime.now(timezone.utc),
    )


def list_complaints(
    db: Session,
    status: str | None = None,
    order_code: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ComplaintListResponse:
    """Paginated list of complaints with optional filters."""
    query = db.query(Complaint)

    if status:
        query = query.filter(Complaint.status == status)
    if order_code:
        query = query.filter(Complaint.order_code == order_code)

    total = query.count()

    complaints = (
        query
        .order_by(Complaint.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        ComplaintListItem(
            id=c.id,
            code=c.code,
            order_code=c.order_code,
            customer_name=c.customer_name,
            customer_email=c.customer_email,
            status=c.status,
            preferred_resolution=c.preferred_resolution,
            photos_count=c.photos_count or 0,
            days_in_current_status=_calculate_days_in_status(c),
            created_at=c.created_at or datetime.now(timezone.utc),
        )
        for c in complaints
    ]

    return ComplaintListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
