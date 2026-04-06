from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional

from app.models.enums import ReturnReason, ReturnStatus
from app.schemas.common import StatusHistoryEntry


class ReturnItemRequest(BaseModel):
    product_code: str
    quantity: int = 1
    reason: ReturnReason
    comment: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v):
        if v < 1:
            raise ValueError("Quantity must be at least 1")
        return v


class ReturnCreateRequest(BaseModel):
    order_code: str
    email: str
    name: str
    phone: Optional[str] = None
    bank_account: Optional[str] = None
    items: list[ReturnItemRequest]

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


class ReturnCreateResponse(BaseModel):
    code: str
    instructions: str
    label_url: Optional[str] = None


class ReturnItemDetail(BaseModel):
    id: int
    product_code: str
    product_name: str
    quantity: int
    unit_price: float
    reason: str
    comment: Optional[str] = None
    refund_amount: float


class ReturnDetailResponse(BaseModel):
    id: int
    code: str
    order_code: str
    customer_email: str
    customer_name: str
    customer_phone: Optional[str] = None
    bank_account: Optional[str] = None
    status: str
    shipping_label_url: Optional[str] = None
    tracking_number: Optional[str] = None
    total_refund_amount: float
    admin_note: Optional[str] = None
    items: list[ReturnItemDetail]
    status_history: list[StatusHistoryEntry]
    days_in_current_status: int
    created_at: datetime
    updated_at: datetime


class ReturnListItem(BaseModel):
    id: int
    code: str
    order_code: str
    customer_name: str
    customer_email: str
    status: str
    total_refund_amount: float
    days_in_current_status: int
    created_at: datetime


class ReturnListResponse(BaseModel):
    items: list[ReturnListItem]
    total: int
    page: int
    page_size: int


class ReturnTrackingResponse(BaseModel):
    code: str
    status: str
    status_label: str
    items: list[ReturnItemDetail]
    status_history: list[StatusHistoryEntry]
    created_at: datetime
