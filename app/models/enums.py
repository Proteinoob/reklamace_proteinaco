import enum


class ReturnStatus(str, enum.Enum):
    NEW = "new"
    WAITING_FOR_DELIVERY = "waiting_for_delivery"
    RECEIVED_INSPECTING = "received_inspecting"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUND_READY = "refund_ready"
    COMPLETED = "completed"


class ComplaintStatus(str, enum.Enum):
    NEW = "new"
    WAITING_FOR_ASSESSMENT = "waiting_for_assessment"
    NEED_MORE_INFO = "need_more_info"
    ASSESSING = "assessing"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class ReturnReason(str, enum.Enum):
    ORDERED_WRONG = "ordered_wrong"
    NOT_SATISFIED = "not_satisfied"
    OTHER = "other"


class PreferredResolution(str, enum.Enum):
    DISCOUNT = "discount"
    NEW_PRODUCT = "new_product"
    REFUND = "refund"
    MISSING_PRODUCT = "missing_product"
    OTHER = "other"
