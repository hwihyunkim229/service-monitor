from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class LossReason(Base):
    __tablename__ = "loss_reason"

    id = Column(Integer, primary_key=True, index=True)
    product = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False, index=True)
    transfer_no = Column(String, nullable=True, index=True)
    loss_type = Column(String, nullable=False)
    loss_count = Column(Integer, nullable=False, default=1)
    reason = Column(String(120), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    created_by = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)