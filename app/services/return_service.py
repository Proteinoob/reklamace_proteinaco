import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.return_request import ReturnRequest, ReturnItem
from app.models.status_history import StatusHistory
from app.models.enums import ReturnStatus, ReturnReason
from app.schemas.return_schemas import (
    ReturnCreateRequest,
    ReturnCreateResponse,
    ReturnDetailResponse,
    ReturnItemDetail,
    ReturnListItem,
    ReturnListResponse,
    ReturnTrackingResponse,
)
from app.schemas.common import (
    OrderLookupRequest,
    OrderLookupResponse,
    OrderProductItem,
    StatusHistoryEntry,
)
from app.services.shoptet_client import ShoptetClient
from app.services.zasilkovna import ZasilkovnaClient, ZasilkovnaError
from app.core.email import EmailService

logger = logging.getLogger(__name__)

# Valid status transitions
RETURN_TRANSITIONS = {
    ReturnStatus.NEW: [ReturnStatus.WAITING_FOR_DELIVERY],
    ReturnStatus.WAITING_FOR_DELIVERY: [ReturnStatus.RECEIVED_INSPECTING],
    ReturnStatus.RECEIVED_INSPECTING: [ReturnStatus.APPROVED, ReturnStatus.REJECTED],
    ReturnStatus.APPROVED: [ReturnStatus.REFUND_READY],
    ReturnStatus.REJECTED: [],
    ReturnStatus.REFUND_READY: [ReturnStatus.COMPLETED],
    ReturnStatus.COMPLETED: [],
}

STATUS_LABELS = {
    ReturnStatus.NEW: "Nová",
    ReturnStatus.WAITING_FOR_DELIVERY: "Čeká na doručení",
    ReturnStatus.RECEIVED_INSPECTING: "Přijato a kontroluje se",
    ReturnStatus.APPROVED: "Schváleno",
    ReturnStatus.REJECTED: "Zamítnuto",
    ReturnStatus.REFUND_READY: "Peníze připraveny k odeslání",
    ReturnStatus.COMPLETED: "Dokončeno",
}


def generate_return_code(db: Session) -> str:
    """Generate a sequential return code: RV-{year}-{NNNN}."""
    current_year = datetime.now(timezone.utc).year
    prefix = f"RV-{current_year}-"

    max_code = (
        db.query(ReturnRequest.code)
        .filter(ReturnRequest.code.like(f"{prefix}%"))
        .order_by(ReturnRequest.code.desc())
        .first()
    )

    if max_code and max_code[0]:
        # Extract the numeric part after the last hyphen
        last_number = int(max_code[0].split("-")[-1])
        next_number = last_number + 1
    else:
        next_number = 1

    return f"{prefix}{next_number:04d}"


async def lookup_order(request: OrderLookupRequest) -> OrderLookupResponse:
    """Look up an order in Shoptet and verify the customer email matches."""
    async with ShoptetClient() as client:
        data = await client.get_order(request.order_code)

    order = data.get("order")
    if not order:
        raise ValueError(f"Objednávka {request.order_code} nebyla nalezena")

    order_email = (order.get("email") or "").lower().strip()
    if order_email != request.email.lower().strip():
        raise ValueError("Email neodpovídá objednávce")

    items = []
    for item in order.get("items", []):
        # Only include product items, skip shipping/billing
        if item.get("itemType") != "product":
            continue
        price_info = item.get("itemPrice", {})
        unit_price = float(price_info.get("withVat", 0)) if isinstance(price_info, dict) else 0.0
        items.append(
            OrderProductItem(
                product_code=item.get("code", ""),
                product_name=item.get("name", ""),
                quantity=int(float(item.get("amount", 1))),
                unit_price=unit_price,
                image_url=item.get("image", None),
            )
        )

    # Customer name: billingAddress.fullName or fallback
    billing = order.get("billingAddress", {}) or {}
    customer_name = billing.get("fullName", "") or order.get("fullName", "")

    return OrderLookupResponse(
        order_code=order.get("code", request.order_code),
        customer_name=customer_name,
        customer_email=order_email,
        order_date=order.get("creationTime"),
        items=items,
    )


async def create_return(
    request: ReturnCreateRequest, db: Session
) -> ReturnCreateResponse:
    """Create a new return request with all associated items."""
    # 1. Lookup order via Shoptet to validate and get product details
    order_lookup = OrderLookupRequest(
        order_code=request.order_code, email=request.email
    )
    order_info = await lookup_order(order_lookup)

    # Build a lookup map: product_code -> OrderProductItem
    product_map = {item.product_code: item for item in order_info.items}

    # 2. Generate code
    code = generate_return_code(db)

    # 3. Create ReturnRequest
    return_req = ReturnRequest(
        code=code,
        order_code=request.order_code,
        customer_email=request.email,
        customer_name=request.name,
        customer_phone=request.phone,
        bank_account=request.bank_account,
        status=ReturnStatus.NEW.value,
    )
    db.add(return_req)
    db.flush()  # Get the ID for items

    # 4. Create items and calculate total refund
    total_refund = 0.0
    for item_req in request.items:
        product_info = product_map.get(item_req.product_code)
        product_name = product_info.product_name if product_info else item_req.product_code
        unit_price = product_info.unit_price if product_info else 0.0
        refund_amount = unit_price * item_req.quantity

        return_item = ReturnItem(
            return_request_id=return_req.id,
            product_code=item_req.product_code,
            product_name=product_name,
            quantity=item_req.quantity,
            unit_price=unit_price,
            reason=item_req.reason.value,
            comment=item_req.comment,
            refund_amount=refund_amount,
        )
        db.add(return_item)
        total_refund += refund_amount

    return_req.total_refund_amount = total_refund

    # 5. Log initial status (NEW)
    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=None,
        new_status=ReturnStatus.NEW.value,
        changed_by="customer",
        note="Žádost o vrácení vytvořena",
    )

    # Transition to WAITING_FOR_DELIVERY
    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=ReturnStatus.NEW.value,
        new_status=ReturnStatus.WAITING_FOR_DELIVERY.value,
        changed_by="system",
        note="Čekáme na doručení zásilky",
    )
    return_req.status = ReturnStatus.WAITING_FOR_DELIVERY.value

    # 6. Try to create Zásilkovna packet
    label_url = None
    try:
        name_parts = request.name.split(" ", 1)
        first_name = name_parts[0]
        surname = name_parts[1] if len(name_parts) > 1 else ""

        async with ZasilkovnaClient() as zas_client:
            packet = await zas_client.create_return_packet(
                case_code=code,
                customer_name=first_name,
                customer_surname=surname,
                customer_email=request.email,
                customer_phone=request.phone or "",
                value=total_refund,
            )
        if packet and packet.get("packet_id"):
            # Store the direct Zásilkovna URL for internal use
            return_req.shipping_label_url = (
                f"https://www.zasilkovna.cz/api/packetLabelPdf"
                f"?packetId={packet['packet_id']}"
            )
            return_req.tracking_number = packet.get("barcode")
            # Public label URL goes through our proxy (Zásilkovna requires API key)
            label_url = f"/api/v1/customer/returns/{code}/label"
    except (ZasilkovnaError, Exception) as e:
        logger.error("Zásilkovna packet creation failed for %s: %s", code, e)

    db.commit()

    # 7. Try to send confirmation email
    try:
        email_service = EmailService()
        items_data = [
            {
                "product_name": item_req.product_code,
                "quantity": item_req.quantity,
                "reason": item_req.reason.value,
            }
            for item_req in request.items
        ]
        await email_service.send_return_confirmation(
            to=request.email,
            return_code=code,
            items=items_data,
            label_url=label_url,
        )
    except Exception as e:
        logger.error("Failed to send confirmation email for %s: %s", code, e)

    # 8. Build response
    instructions = (
        f"Vaše žádost o vrácení {code} byla přijata. "
        "Zabalte prosím zboží a odešlete ho na naši adresu."
    )
    if label_url:
        instructions += f" Štítek pro Zásilkovnu najdete na: {label_url}"

    return ReturnCreateResponse(
        code=code,
        instructions=instructions,
        label_url=label_url,
    )


def _validate_transition(current_status_value: str, target_status: ReturnStatus):
    """Validate that a status transition is allowed."""
    try:
        current = ReturnStatus(current_status_value)
    except ValueError:
        raise ValueError(f"Neznámý aktuální stav: {current_status_value}")

    allowed = RETURN_TRANSITIONS.get(current, [])
    if target_status not in allowed:
        raise ValueError(
            f"Nelze přejít ze stavu '{current.value}' do '{target_status.value}'"
        )


def receive_return(
    return_id: int, db: Session, admin_user: str
) -> ReturnRequest:
    """Mark a return as received and begin inspection."""
    return_req = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_req:
        raise ValueError(f"Vrácení s ID {return_id} nebylo nalezeno")

    _validate_transition(return_req.status, ReturnStatus.RECEIVED_INSPECTING)

    old_status = return_req.status
    return_req.status = ReturnStatus.RECEIVED_INSPECTING.value

    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=old_status,
        new_status=ReturnStatus.RECEIVED_INSPECTING.value,
        changed_by=admin_user,
        note="Zásilka přijata, probíhá kontrola",
    )

    db.commit()

    # Send status change email (fire-and-forget style, but we're sync here)
    # Email sending is handled by the caller/API layer if needed
    return return_req


async def approve_return(
    return_id: int, db: Session, admin_user: str
) -> ReturnRequest:
    """Approve a return and attempt to create a credit note in Shoptet."""
    return_req = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_req:
        raise ValueError(f"Vrácení s ID {return_id} nebylo nalezeno")

    _validate_transition(return_req.status, ReturnStatus.APPROVED)

    old_status = return_req.status
    return_req.status = ReturnStatus.APPROVED.value

    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=old_status,
        new_status=ReturnStatus.APPROVED.value,
        changed_by=admin_user,
        note="Vrácení schváleno",
    )

    # Try to create credit note via Shoptet
    try:
        async with ShoptetClient() as client:
            order_data = await client.get_order(return_req.order_code)
            invoices = order_data.get("order", {}).get("invoices", [])
            if invoices:
                invoice_code = invoices[0].get("code")
                if invoice_code:
                    await client.create_credit_note(invoice_code)
                    logger.info(
                        "Credit note created for invoice %s (return %s)",
                        invoice_code,
                        return_req.code,
                    )
    except Exception as e:
        logger.error(
            "Failed to create credit note for return %s: %s",
            return_req.code,
            e,
        )

    # Auto-transition to REFUND_READY
    return_req.status = ReturnStatus.REFUND_READY.value
    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=ReturnStatus.APPROVED.value,
        new_status=ReturnStatus.REFUND_READY.value,
        changed_by="system",
        note="Peníze připraveny k odeslání",
    )

    db.commit()

    # Send email with refund details
    try:
        email_service = EmailService()
        await email_service.send_status_change(
            to=return_req.customer_email,
            case_code=return_req.code,
            case_type="return",
            new_status=ReturnStatus.REFUND_READY.value,
            status_label=STATUS_LABELS[ReturnStatus.REFUND_READY],
        )
    except Exception as e:
        logger.error(
            "Failed to send approval email for %s: %s", return_req.code, e
        )

    return return_req


def reject_return(
    return_id: int, reason: str, db: Session, admin_user: str
) -> ReturnRequest:
    """Reject a return with the given reason."""
    return_req = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_req:
        raise ValueError(f"Vrácení s ID {return_id} nebylo nalezeno")

    _validate_transition(return_req.status, ReturnStatus.REJECTED)

    old_status = return_req.status
    return_req.status = ReturnStatus.REJECTED.value
    return_req.admin_note = reason

    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=old_status,
        new_status=ReturnStatus.REJECTED.value,
        changed_by=admin_user,
        note=f"Zamítnuto: {reason}",
    )

    db.commit()
    return return_req


def mark_refunded(
    return_id: int, db: Session, admin_user: str
) -> ReturnRequest:
    """Mark a return as completed (refund sent)."""
    return_req = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_req:
        raise ValueError(f"Vrácení s ID {return_id} nebylo nalezeno")

    _validate_transition(return_req.status, ReturnStatus.COMPLETED)

    old_status = return_req.status
    return_req.status = ReturnStatus.COMPLETED.value

    _log_status_change(
        db,
        entity_type="return",
        entity_id=return_req.id,
        old_status=old_status,
        new_status=ReturnStatus.COMPLETED.value,
        changed_by=admin_user,
        note="Peníze odeslány, vrácení dokončeno",
    )

    db.commit()
    return return_req


def get_return_detail(return_id: int, db: Session) -> ReturnDetailResponse:
    """Load full return detail including items and status history."""
    return_req = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
    if not return_req:
        raise ValueError(f"Vrácení s ID {return_id} nebylo nalezeno")

    items = [
        ReturnItemDetail(
            id=item.id,
            product_code=item.product_code,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            reason=item.reason,
            comment=item.comment,
            refund_amount=item.refund_amount,
        )
        for item in return_req.items
    ]

    history_entries = (
        db.query(StatusHistory)
        .filter(
            StatusHistory.entity_type == "return",
            StatusHistory.entity_id == return_req.id,
        )
        .order_by(StatusHistory.created_at.asc())
        .all()
    )

    status_history = [
        StatusHistoryEntry(
            old_status=entry.old_status,
            new_status=entry.new_status,
            changed_by=entry.changed_by,
            note=entry.note,
            created_at=entry.created_at,
        )
        for entry in history_entries
    ]

    days = _calculate_days_in_status(return_req, db)

    return ReturnDetailResponse(
        id=return_req.id,
        code=return_req.code,
        order_code=return_req.order_code,
        customer_email=return_req.customer_email,
        customer_name=return_req.customer_name,
        customer_phone=return_req.customer_phone,
        bank_account=return_req.bank_account,
        status=return_req.status,
        shipping_label_url=return_req.shipping_label_url,
        tracking_number=return_req.tracking_number,
        total_refund_amount=return_req.total_refund_amount,
        admin_note=return_req.admin_note,
        items=items,
        status_history=status_history,
        days_in_current_status=days,
        created_at=return_req.created_at,
        updated_at=return_req.updated_at,
    )


def get_return_by_code(
    code: str, email: str, db: Session
) -> ReturnTrackingResponse:
    """Get return tracking info for a customer. Verify email matches."""
    return_req = (
        db.query(ReturnRequest).filter(ReturnRequest.code == code).first()
    )
    if not return_req:
        raise ValueError(f"Vrácení s kódem {code} nebylo nalezeno")

    if return_req.customer_email.lower().strip() != email.lower().strip():
        raise ValueError("Email neodpovídá záznamu vrácení")

    items = [
        ReturnItemDetail(
            id=item.id,
            product_code=item.product_code,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            reason=item.reason,
            comment=item.comment,
            refund_amount=item.refund_amount,
        )
        for item in return_req.items
    ]

    history_entries = (
        db.query(StatusHistory)
        .filter(
            StatusHistory.entity_type == "return",
            StatusHistory.entity_id == return_req.id,
        )
        .order_by(StatusHistory.created_at.asc())
        .all()
    )

    status_history = [
        StatusHistoryEntry(
            old_status=entry.old_status,
            new_status=entry.new_status,
            changed_by=entry.changed_by,
            note=entry.note,
            created_at=entry.created_at,
        )
        for entry in history_entries
    ]

    status_enum = ReturnStatus(return_req.status)
    status_label = STATUS_LABELS.get(status_enum, return_req.status)

    return ReturnTrackingResponse(
        code=return_req.code,
        status=return_req.status,
        status_label=status_label,
        items=items,
        status_history=status_history,
        created_at=return_req.created_at,
    )


def list_returns(
    db: Session,
    status: str = None,
    order_code: str = None,
    page: int = 1,
    page_size: int = 20,
) -> ReturnListResponse:
    """Paginated list of returns with optional filters."""
    query = db.query(ReturnRequest)

    if status:
        query = query.filter(ReturnRequest.status == status)
    if order_code:
        query = query.filter(ReturnRequest.order_code == order_code)

    total = query.count()

    returns = (
        query.order_by(ReturnRequest.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        ReturnListItem(
            id=r.id,
            code=r.code,
            order_code=r.order_code,
            customer_name=r.customer_name,
            customer_email=r.customer_email,
            status=r.status,
            total_refund_amount=r.total_refund_amount,
            days_in_current_status=_calculate_days_in_status(r, db),
            created_at=r.created_at,
        )
        for r in returns
    ]

    return ReturnListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


def _log_status_change(
    db: Session,
    entity_type: str,
    entity_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str,
    note: str | None = None,
):
    """Create a StatusHistory record."""
    entry = StatusHistory(
        entity_type=entity_type,
        entity_id=entity_id,
        old_status=old_status,
        new_status=new_status,
        changed_by=changed_by,
        note=note,
    )
    db.add(entry)
    db.flush()


def _calculate_days_in_status(return_req: ReturnRequest, db: Session) -> int:
    """Calculate how many days the return has been in its current status."""
    last_entry = (
        db.query(StatusHistory)
        .filter(
            StatusHistory.entity_type == "return",
            StatusHistory.entity_id == return_req.id,
        )
        .order_by(StatusHistory.created_at.desc())
        .first()
    )

    if last_entry and last_entry.created_at:
        now = datetime.now(timezone.utc)
        # Handle naive datetimes from SQLite
        last_time = last_entry.created_at
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        delta = now - last_time
        return delta.days
    return 0
