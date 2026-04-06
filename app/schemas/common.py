from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


class OrderLookupRequest(BaseModel):
    order_code: str
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if "@" not in v:
            raise ValueError("Invalid email format")
        return v.lower().strip()

    @field_validator("order_code")
    @classmethod
    def validate_order_code(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Order code is required")
        return v


class OrderProductItem(BaseModel):
    product_code: str
    product_name: str
    quantity: int
    unit_price: float
    image_url: Optional[str] = None


class OrderLookupResponse(BaseModel):
    order_code: str
    customer_name: str
    customer_email: str
    order_date: Optional[str] = None
    items: list[OrderProductItem]


class StatusHistoryEntry(BaseModel):
    old_status: Optional[str] = None
    new_status: str
    changed_by: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int
