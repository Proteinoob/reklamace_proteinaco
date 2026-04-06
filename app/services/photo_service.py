import logging
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.complaint import Complaint, ComplaintPhoto
from app.models.enums import ComplaintStatus

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_COMPLAINT = 5
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_TOTAL_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_DIMENSION = 1920
JPEG_QUALITY = 80
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/jpg"}


class PhotoValidationError(Exception):
    pass


def compress_image(file_data: bytes) -> bytes:
    """Compress and resize image. Returns JPEG bytes."""
    img = Image.open(BytesIO(file_data))

    # Convert to RGB if necessary (e.g., PNG with alpha)
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    # Resize if too large
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    # Save as JPEG
    output = BytesIO()
    img.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()


def validate_photo(file_data: bytes, content_type: str, existing_count: int) -> None:
    """Validate photo before saving. Raises PhotoValidationError."""
    if existing_count >= MAX_PHOTOS_PER_COMPLAINT:
        raise PhotoValidationError(
            f"Maximum {MAX_PHOTOS_PER_COMPLAINT} photos per complaint"
        )

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise PhotoValidationError(
            f"Invalid file type: {content_type}. Allowed: JPEG, PNG"
        )

    if len(file_data) > MAX_FILE_SIZE_BYTES:
        raise PhotoValidationError(
            f"File too large: {len(file_data) / 1024 / 1024:.1f} MB. Maximum: 5 MB"
        )


def save_photo(
    complaint_id: int,
    file_data: bytes,
    original_filename: str,
    content_type: str,
    db: Session,
) -> ComplaintPhoto:
    """Validate, compress, save photo to disk and create DB record."""
    # Count existing photos
    existing_count = (
        db.query(ComplaintPhoto)
        .filter(ComplaintPhoto.complaint_id == complaint_id)
        .count()
    )

    validate_photo(file_data, content_type, existing_count)

    # Compress
    compressed = compress_image(file_data)

    # Save to disk
    upload_dir = Path(settings.UPLOAD_DIR) / str(complaint_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex
    file_path = upload_dir / f"{file_id}.jpg"
    file_path.write_bytes(compressed)

    # Create DB record
    photo = ComplaintPhoto(
        complaint_id=complaint_id,
        file_path=str(file_path),
        original_filename=original_filename,
    )
    db.add(photo)

    # Update photos_count on complaint
    complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if complaint:
        complaint.photos_count = existing_count + 1

    db.commit()
    db.refresh(photo)

    logger.info(f"Photo saved: {file_path} ({len(compressed)} bytes)")
    return photo


def get_photo_path(complaint_id: int, photo_id: int, db: Session) -> Path | None:
    """Get file path for a specific photo."""
    photo = (
        db.query(ComplaintPhoto)
        .filter(
            ComplaintPhoto.id == photo_id,
            ComplaintPhoto.complaint_id == complaint_id,
        )
        .first()
    )
    if photo is None:
        return None
    path = Path(photo.file_path)
    return path if path.exists() else None


def cleanup_old_photos(db: Session, months: int = 3) -> int:
    """Delete photos from completed/resolved complaints older than N months.

    Updates complaint records with photos_count and photos_deleted_at.
    Returns number of complaints cleaned up.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)

    # Find complaints that are resolved, older than cutoff, with photos not yet deleted
    complaints = (
        db.query(Complaint)
        .filter(
            Complaint.status.in_([
                ComplaintStatus.RESOLVED.value,
            ]),
            Complaint.updated_at < cutoff,
            Complaint.photos_deleted_at.is_(None),
            Complaint.photos_count > 0,
        )
        .all()
    )

    cleaned = 0
    for complaint in complaints:
        # Delete files from disk
        upload_dir = Path(settings.UPLOAD_DIR) / str(complaint.id)
        if upload_dir.exists():
            for file in upload_dir.iterdir():
                file.unlink()
            upload_dir.rmdir()
            logger.info(f"Deleted photos for complaint {complaint.code}")

        # Delete photo records from DB
        db.query(ComplaintPhoto).filter(
            ComplaintPhoto.complaint_id == complaint.id
        ).delete()

        # Update complaint record -- keep photos_count as historical record
        complaint.photos_deleted_at = datetime.now(timezone.utc)
        cleaned += 1

    if cleaned:
        db.commit()
        logger.info(f"Cleaned up photos for {cleaned} complaints")

    return cleaned
