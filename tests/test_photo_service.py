import pytest
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.models.complaint import Complaint, ComplaintPhoto
from app.models.enums import ComplaintStatus
from app.services.photo_service import (
    MAX_DIMENSION,
    MAX_PHOTOS_PER_COMPLAINT,
    PhotoValidationError,
    compress_image,
    cleanup_old_photos,
    get_photo_path,
    save_photo,
    validate_photo,
)


def make_test_image(width=100, height=100, color="red", fmt="JPEG") -> bytes:
    """Create a test image and return its bytes."""
    img = Image.new("RGB", (width, height), color)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def make_rgba_png(width=100, height=100) -> bytes:
    """Create a test RGBA PNG image."""
    img = Image.new("RGBA", (width, height), (255, 0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_complaint(db, status=ComplaintStatus.NEW.value, **kwargs):
    """Helper to create a Complaint record in the database."""
    defaults = dict(
        code="RE-2026-0001",
        order_code="OBJ-12345",
        customer_email="test@example.com",
        customer_name="Test User",
        status=status,
        photos_count=0,
    )
    defaults.update(kwargs)
    complaint = Complaint(**defaults)
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


# --- compress_image tests ---


class TestCompressImage:
    def test_compress_image_large(self):
        """Large image (3000x2000) gets resized to fit within 1920."""
        data = make_test_image(3000, 2000)
        result = compress_image(data)

        # Output should be valid JPEG
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.width <= MAX_DIMENSION
        assert img.height <= MAX_DIMENSION

    def test_compress_image_small(self):
        """Image smaller than MAX_DIMENSION stays the same size."""
        data = make_test_image(800, 600)
        result = compress_image(data)

        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.width == 800
        assert img.height == 600

    def test_compress_png_with_alpha(self):
        """PNG RGBA image converts to RGB JPEG without error."""
        data = make_rgba_png(200, 200)
        result = compress_image(data)

        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.width == 200
        assert img.height == 200


# --- validate_photo tests ---


class TestValidatePhoto:
    def test_validate_photo_success(self):
        """Valid photo params pass without error."""
        data = make_test_image()
        # Should not raise
        validate_photo(data, "image/jpeg", existing_count=0)

    def test_validate_photo_max_count(self):
        """Raises error when max photos reached."""
        data = make_test_image()
        with pytest.raises(PhotoValidationError, match="Maximum 5"):
            validate_photo(data, "image/jpeg", existing_count=MAX_PHOTOS_PER_COMPLAINT)

    def test_validate_photo_invalid_type(self):
        """Raises error for non-image content type."""
        data = make_test_image()
        with pytest.raises(PhotoValidationError, match="Invalid file type"):
            validate_photo(data, "text/plain", existing_count=0)

    def test_validate_photo_too_large(self):
        """Raises error for file exceeding 5 MB."""
        data = b"\x00" * (6 * 1024 * 1024)  # 6 MB
        with pytest.raises(PhotoValidationError, match="File too large"):
            validate_photo(data, "image/jpeg", existing_count=0)


# --- save_photo tests ---


class TestSavePhoto:
    def test_save_photo(self, db_session, tmp_path):
        """Save photo creates file on disk and DB record."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            complaint = create_complaint(db_session)
            data = make_test_image()

            photo = save_photo(
                complaint_id=complaint.id,
                file_data=data,
                original_filename="test.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

            assert photo.id is not None
            assert photo.complaint_id == complaint.id
            assert photo.original_filename == "test.jpg"
            assert Path(photo.file_path).exists()

    def test_save_photo_updates_count(self, db_session, tmp_path):
        """Saving a photo increments complaint.photos_count."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            complaint = create_complaint(db_session)
            assert complaint.photos_count == 0

            save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="photo1.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

            db_session.refresh(complaint)
            assert complaint.photos_count == 1

            save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="photo2.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

            db_session.refresh(complaint)
            assert complaint.photos_count == 2


# --- get_photo_path tests ---


class TestGetPhotoPath:
    def test_get_photo_path_exists(self, db_session, tmp_path):
        """Returns Path when photo file exists on disk."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            complaint = create_complaint(db_session)
            photo = save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="exists.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

            result = get_photo_path(complaint.id, photo.id, db_session)
            assert result is not None
            assert result.exists()

    def test_get_photo_path_missing(self, db_session):
        """Returns None for non-existent photo."""
        complaint = create_complaint(db_session)
        result = get_photo_path(complaint.id, 9999, db_session)
        assert result is None


# --- cleanup_old_photos tests ---


class TestCleanupOldPhotos:
    def test_cleanup_old_photos(self, db_session, tmp_path):
        """Cleanup deletes files, sets photos_deleted_at, preserves photos_count."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            # Create a resolved complaint with an old updated_at
            complaint = create_complaint(
                db_session,
                status=ComplaintStatus.RESOLVED.value,
            )

            # Save a photo
            photo = save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="old.jpg",
                content_type="image/jpeg",
                db=db_session,
            )
            saved_path = Path(photo.file_path)
            assert saved_path.exists()

            # Backdate updated_at to 4 months ago
            old_date = datetime.now(timezone.utc) - timedelta(days=130)
            complaint.updated_at = old_date
            db_session.commit()

            # Run cleanup
            cleaned = cleanup_old_photos(db_session, months=3)

            assert cleaned == 1
            assert not saved_path.exists()

            db_session.refresh(complaint)
            assert complaint.photos_deleted_at is not None
            # photos_count preserved as historical record
            assert complaint.photos_count == 1

            # Photo records deleted from DB
            remaining = (
                db_session.query(ComplaintPhoto)
                .filter(ComplaintPhoto.complaint_id == complaint.id)
                .count()
            )
            assert remaining == 0

    def test_cleanup_skips_recent(self, db_session, tmp_path):
        """Cleanup does not touch recently resolved complaints."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            complaint = create_complaint(
                db_session,
                status=ComplaintStatus.RESOLVED.value,
            )

            photo = save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="recent.jpg",
                content_type="image/jpeg",
                db=db_session,
            )
            saved_path = Path(photo.file_path)

            # updated_at is recent (just committed), so cleanup should skip it
            cleaned = cleanup_old_photos(db_session, months=3)

            assert cleaned == 0
            assert saved_path.exists()

    def test_cleanup_skips_non_resolved(self, db_session, tmp_path):
        """Cleanup does not touch complaints that are not resolved."""
        with patch("app.services.photo_service.settings") as mock_settings:
            mock_settings.UPLOAD_DIR = str(tmp_path / "uploads")

            complaint = create_complaint(
                db_session,
                status=ComplaintStatus.NEW.value,
            )

            photo = save_photo(
                complaint_id=complaint.id,
                file_data=make_test_image(),
                original_filename="new.jpg",
                content_type="image/jpeg",
                db=db_session,
            )

            # Backdate it
            old_date = datetime.now(timezone.utc) - timedelta(days=130)
            complaint.updated_at = old_date
            db_session.commit()

            cleaned = cleanup_old_photos(db_session, months=3)
            assert cleaned == 0
