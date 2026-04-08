from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional

from app.models.enums import PreferredResolution
from app.schemas.common import StatusHistoryEntry


class ComplaintItemRequest(BaseModel):
    product_code: str
    quantity: int = 1
    problem_description: str
    doses_taken: Optional[int] = None
    discovery_date: Optional[str] = None
    preferred_resolution: PreferredResolution = PreferredResolution.REFUND

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v < 1:
            raise ValueError("Quantity must be at least 1")
        return v


class ComplaintCreateRequest(BaseModel):
    order_code: str
    email: str
    name: str
    phone: Optional[str] = None
    bank_account: Optional[str] = None
    items: list[ComplaintItemRequest]

    @field_validator("items")
    @classmethod
    def validate_items(cls, v):
        if not v:
            raise ValueError("At least one item is required")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v:
            raise ValueError("Invalid email")
        return v.lower().strip()


class ComplaintCreateResponse(BaseModel):
    code: str
    instructions: str
    label_url: Optional[str] = None
    coupon_code: Optional[str] = None
    preferred_resolution: Optional[str] = None


class ComplaintItemDetail(BaseModel):
    id: int
    product_code: str
    product_name: str
    quantity: int
    unit_price: float
    problem_description: str
    doses_taken: Optional[int] = None
    discovery_date: Optional[str] = None
    refund_amount: Optional[float] = None


class ComplaintPhotoDetail(BaseModel):
    id: int
    original_filename: Optional[str] = None
    uploaded_at: datetime


class ComplaintDetailResponse(BaseModel):
    id: int
    code: str
    order_code: str
    customer_email: str
    customer_name: str
    customer_phone: Optional[str] = None
    bank_account: Optional[str] = None
    status: str
    preferred_resolution: Optional[str] = None
    shipping_label_url: Optional[str] = None
    tracking_number: Optional[str] = None
    admin_note: Optional[str] = None
    photos_count: int
    items: list[ComplaintItemDetail]
    photos: list[ComplaintPhotoDetail]
    status_history: list[StatusHistoryEntry]
    days_in_current_status: int
    created_at: datetime
    updated_at: datetime


class ComplaintListItem(BaseModel):
    id: int
    code: str
    order_code: str
    customer_name: str
    customer_email: str
    status: str
    preferred_resolution: Optional[str] = None
    photos_count: int
    days_in_current_status: int
    created_at: datetime


class ComplaintListResponse(BaseModel):
    items: list[ComplaintListItem]
    total: int
    page: int
    page_size: int


class ComplaintTrackingResponse(BaseModel):
    code: str
    status: str
    status_label: str
    items: list[ComplaintItemDetail]
    photos: list[ComplaintPhotoDetail]
    status_history: list[StatusHistoryEntry]
    coupon_code: Optional[str] = None
    created_at: datetime


class ComplaintSupplementRequest(BaseModel):
    email: str
    message: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v:
            raise ValueError("Invalid email")
        return v.lower().strip()

    @field_validator("message")
    @classmethod
    def validate_message(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Message is required")
        return v


class AdminRequestInfoRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def validate_message(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Message is required")
        return v


class AdminApproveComplaintRequest(BaseModel):
    resolution: PreferredResolution
    note: Optional[str] = None


class AdminRejectRequest(BaseModel):
    """Shared for both return and complaint rejection."""
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Reason is required")
        return v
