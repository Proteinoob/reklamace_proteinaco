import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.common import OrderLookupRequest, OrderLookupResponse
from app.schemas.return_schemas import (
    ReturnCreateRequest,
    ReturnCreateResponse,
    ReturnTrackingResponse,
)
from app.schemas.complaint_schemas import (
    ComplaintCreateRequest,
    ComplaintCreateResponse,
    ComplaintTrackingResponse,
    ComplaintSupplementRequest,
)
from app.services import return_service, complaint_service
from app.services.photo_service import MAX_PHOTOS_PER_COMPLAINT, PhotoValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/customer", tags=["customer"])

# Simple in-memory rate limiter
_rate_limits: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str, limit: int, window: int = 60):
    """Check rate limit. Raises HTTPException(429) if exceeded."""
    now = time.time()
    _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < window]
    if len(_rate_limits[ip]) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
        )
    _rate_limits[ip].append(now)


# --------------- Lookup order ---------------


@router.post("/lookup-order", response_model=OrderLookupResponse)
async def lookup_order(
    body: OrderLookupRequest,
    request: Request,
):
    """Look up an order by code and email. No auth required."""
    _check_rate_limit(request.client.host, limit=10)
    try:
        result = await return_service.lookup_order(body)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("lookup_order failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Create return ---------------


@router.post("/returns", response_model=ReturnCreateResponse)
async def create_return(
    body: ReturnCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new return request."""
    _check_rate_limit(request.client.host, limit=5)
    try:
        result = await return_service.create_return(body, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("create_return failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Create complaint ---------------


@router.post("/complaints", response_model=ComplaintCreateResponse)
async def create_complaint(
    body: ComplaintCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new complaint."""
    _check_rate_limit(request.client.host, limit=5)
    try:
        result = await complaint_service.create_complaint(body, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("create_complaint failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Upload photos ---------------


@router.post("/complaints/{code}/photos")
async def upload_photos(
    code: str,
    request: Request,
    email: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Upload photos for a complaint (max 5 files)."""
    _check_rate_limit(request.client.host, limit=10)

    if len(files) > MAX_PHOTOS_PER_COMPLAINT:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_PHOTOS_PER_COMPLAINT} photos allowed per upload",
        )

    try:
        file_tuples = []
        for f in files:
            data = await f.read()
            file_tuples.append((data, f.filename or "photo.jpg", f.content_type or "image/jpeg"))

        photos = await complaint_service.upload_photos(code, email, file_tuples, db)
        return {
            "uploaded": len(photos),
            "message": f"{len(photos)} photo(s) uploaded successfully",
        }
    except PhotoValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("upload_photos failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Track return ---------------


@router.get("/returns/{code}", response_model=ReturnTrackingResponse)
async def track_return(
    code: str,
    email: str,
    db: Session = Depends(get_db),
):
    """Get return tracking info. Email is used as authentication."""
    try:
        result = return_service.get_return_by_code(code, email, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("track_return failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Download return label ---------------


@router.get("/returns/{code}/label")
async def download_return_label(
    code: str,
    db: Session = Depends(get_db),
):
    """Download Zásilkovna shipping label PDF for a return."""
    from app.models.return_request import ReturnRequest
    from app.services.zasilkovna import ZasilkovnaClient, ZasilkovnaError

    return_req = db.query(ReturnRequest).filter(ReturnRequest.code == code).first()
    if not return_req:
        raise HTTPException(status_code=404, detail="Vrácení nenalezeno")
    if not return_req.tracking_number:
        raise HTTPException(status_code=404, detail="Štítek není k dispozici")

    # Extract packet_id from tracking data or use the stored barcode
    # The barcode from Zásilkovna is the packet_id for label retrieval
    packet_id = return_req.tracking_number
    # If we stored the numeric packet_id separately, prefer it
    if return_req.shipping_label_url and "packetId=" in return_req.shipping_label_url:
        packet_id = return_req.shipping_label_url.split("packetId=")[-1]

    try:
        async with ZasilkovnaClient() as client:
            pdf_data = await client.get_label_pdf(packet_id)
        return Response(
            content=pdf_data,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename=stitek-{code}.pdf"},
        )
    except ZasilkovnaError as exc:
        raise HTTPException(status_code=502, detail=f"Zásilkovna error: {exc}")
    except Exception as exc:
        logger.error("download_return_label failed: %s", exc)
        raise HTTPException(status_code=500, detail="Nepodařilo se stáhnout štítek")


# --------------- Track complaint ---------------


@router.get("/complaints/{code}", response_model=ComplaintTrackingResponse)
async def track_complaint(
    code: str,
    email: str,
    db: Session = Depends(get_db),
):
    """Get complaint tracking info. Email is used as authentication."""
    try:
        result = complaint_service.get_complaint_by_code(code, email, db)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("track_complaint failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")


# --------------- Supplement complaint ---------------


@router.post("/complaints/{code}/supplement")
async def supplement_complaint(
    code: str,
    body: ComplaintSupplementRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Customer supplements a complaint with additional info."""
    _check_rate_limit(request.client.host, limit=5)
    try:
        complaint = await complaint_service.supplement_complaint(
            complaint_code=code,
            email=body.email,
            message=body.message,
            db=db,
        )
        return {
            "code": complaint.code,
            "status": complaint.status,
            "message": "Supplement submitted successfully",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("supplement_complaint failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error")
