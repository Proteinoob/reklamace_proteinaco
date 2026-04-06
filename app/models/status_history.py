from datetime import datetime

from sqlalchemy import Column, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from app.core.database import Base


class StatusHistory(Base):
    __tablename__ = "status_history"
    __table_args__ = (
        Index("ix_status_history_entity", "entity_type", "entity_id"),
    )

    id = Column(Integer, primary_key=True)
    entity_type = Column(String, nullable=False)  # "return" or "complaint"
    entity_id = Column(Integer, nullable=False)
    old_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)
    changed_by = Column(String, nullable=True)  # admin username or "customer"
    note = Column(Text, nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True), default=datetime.utcnow
    )
