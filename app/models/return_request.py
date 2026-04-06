from datetime import datetime

from sqlalchemy import (
    Column,
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
from app.models.enums import ReturnStatus


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    __table_args__ = (
        Index("ix_return_requests_status", "status"),
        Index("ix_return_requests_order_code", "order_code"),
    )

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)  # RV-YYYY-NNNN
    eshop_id = Column(String, default="228312")
    order_code = Column(String, nullable=False)
    customer_email = Column(String, nullable=False)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=True)
    bank_account = Column(String, nullable=True)
    status = Column(String, default=ReturnStatus.NEW.value)
    shipping_label_url = Column(String, nullable=True)
    tracking_number = Column(String, nullable=True)
    total_refund_amount = Column(Float, default=0)
    admin_note = Column(Text, nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    items = relationship(
        "ReturnItem",
        back_populates="return_request",
        cascade="all, delete-orphan",
    )
    status_history = relationship(
        "StatusHistory",
        primaryjoin=(
            "and_(foreign(StatusHistory.entity_id) == ReturnRequest.id, "
            "StatusHistory.entity_type == 'return')"
        ),
        viewonly=True,
    )


class ReturnItem(Base):
    __tablename__ = "return_items"

    id = Column(Integer, primary_key=True)
    return_request_id = Column(
        Integer, ForeignKey("return_requests.id"), nullable=False
    )
    product_code = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, nullable=False)
    reason = Column(String, nullable=False)  # ReturnReason enum value
    comment = Column(Text, nullable=True)
    refund_amount = Column(Float, nullable=False)

    return_request = relationship("ReturnRequest", back_populates="items")
