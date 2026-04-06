from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.enums import ComplaintStatus


class Complaint(Base):
    __tablename__ = "complaints"
    __table_args__ = (
        Index("ix_complaints_status", "status"),
        Index("ix_complaints_order_code", "order_code"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # RE-YYYY-NNNN
    eshop_id = Column(String, default="228312")
    order_code = Column(String, nullable=False)
    customer_email = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=True)
    bank_account = Column(String, nullable=True)
    status = Column(String, default=ComplaintStatus.NEW.value)
    preferred_resolution = Column(String, nullable=True)
    shipping_label_url = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    admin_note = Column(Text, nullable=True)
    photos_count = Column(Integer, default=0)
    photos_deleted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    items = relationship(
        "ComplaintItem",
        back_populates="complaint",
        cascade="all, delete-orphan",
    )
    photos = relationship(
        "ComplaintPhoto",
        back_populates="complaint",
        cascade="all, delete-orphan",
    )
    status_history = relationship(
        "StatusHistory",
        primaryjoin=(
            "and_(foreign(StatusHistory.entity_id) == Complaint.id, "
            "StatusHistory.entity_type == 'complaint')"
        ),
        viewonly=True,
    )


class ComplaintItem(Base):
    __tablename__ = "complaint_items"

    id = Column(Integer, primary_key=True)
    complaint_id = Column(
        Integer, ForeignKey("complaints.id"), nullable=False
    )
    product_code = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, nullable=False)
    problem_description = Column(Text, nullable=False)
    doses_taken = Column(Integer, nullable=True)
    discovery_date = Column(Date, nullable=True)
    refund_amount = Column(Float, nullable=True)

    complaint = relationship("Complaint", back_populates="items")


class ComplaintPhoto(Base):
    __tablename__ = "complaint_photos"

    id = Column(Integer, primary_key=True)
    complaint_id = Column(
        Integer, ForeignKey("complaints.id"), nullable=False
    )
    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    uploaded_at = Column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )

    complaint = relationship("Complaint", back_populates="photos")
