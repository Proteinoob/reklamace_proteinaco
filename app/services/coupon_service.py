"""
Coupon service — creates discount coupons via Shoptet API.
Rule: max 1 coupon per order_code.
"""
import logging
import secrets
import string

from sqlalchemy.orm import Session

from app.models.complaint import Complaint
from app.services.shoptet_client import ShoptetClient

logger = logging.getLogger(__name__)

# Shoptet coupon template UUID (from existing coupons in the eshop)
COUPON_TEMPLATE = "04a9b7ac-869c-11e9-beb1-002590dad85e"


def _generate_coupon_code(complaint_code: str) -> str:
    """Generate a unique coupon code, max 16 chars. E.g. RK-0001-A3X7."""
    # Extract the number part from RE-2026-0001
    num_part = complaint_code.split("-")[-1] if "-" in complaint_code else complaint_code[-4:]
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"RK-{num_part}-{suffix}"


def has_coupon_for_order(order_code: str, db: Session) -> str | None:
    """Check if a coupon was already issued for this order. Returns coupon_code or None."""
    existing = (
        db.query(Complaint.coupon_code)
        .filter(
            Complaint.order_code == order_code,
            Complaint.coupon_code.isnot(None),
        )
        .first()
    )
    return existing[0] if existing else None


async def create_coupon(
    complaint_code: str,
    order_code: str,
    amount: float,
    db: Session,
    shoptet_client: ShoptetClient | None = None,
) -> str | None:
    """Create a Shoptet discount coupon for a complaint.

    Returns coupon code or None on failure.
    Enforces 1 coupon per order_code.
    """
    # Check if coupon already exists for this order
    existing = has_coupon_for_order(order_code, db)
    if existing:
        logger.info("Coupon already exists for order %s: %s", order_code, existing)
        return existing

    coupon_code = _generate_coupon_code(complaint_code)

    # 15% discount, single-use, no expiration
    DISCOUNT_PERCENT = 15
    # Shoptet uses "ratio" for percentual discounts: 0.85 = 15% off
    ratio_str = f"{(100 - DISCOUNT_PERCENT) / 100:.4f}"

    client = shoptet_client or ShoptetClient()
    try:
        http = await client._get_client()
        coupon_data = {
            "code": coupon_code,
            "discountType": "percentual",
            "ratio": ratio_str,
            "reusable": False,
            "remark": f"Reklamace {complaint_code}, obj. {order_code}",
            "template": COUPON_TEMPLATE,
            "shippingPrice": "beforeDiscount",
        }

        resp = await http.post(
            "/api/discount-coupons",
            json={"data": {"coupons": [coupon_data]}},
        )
        data = resp.json()

        if not resp.is_success or data.get("errors"):
            logger.error("Shoptet coupon creation failed (HTTP %s): %s", resp.status_code, data.get("errors"))
            return None

        logger.info("Coupon %s created for complaint %s (15%% off)", coupon_code, complaint_code)
        return coupon_code

    except Exception as exc:
        logger.error("Failed to create coupon for %s: %s", complaint_code, exc)
        return None
