from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class ReturnTransferRequest(Base):
    __tablename__ = "return_transfer_request"

    id = Column(Integer, primary_key=True)
    product = Column(String, nullable=False, index=True)
    request_type = Column(String, nullable=False)  # set, ring, cradle
    status = Column(String, nullable=False, default="requested", index=True)
    rejection_reason = Column(Text, nullable=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    urgent = Column(Boolean, nullable=False, default=False)
    items = relationship(
        "ReturnTransferItem", back_populates="request", cascade="all, delete-orphan"
    )
    history = relationship(
        "ReturnTransferHistory", back_populates="request", cascade="all, delete-orphan"
    )


class ReturnTransferItem(Base):
    __tablename__ = "return_transfer_item"

    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("return_transfer_request.id"), nullable=False, index=True
    )
    cs_id = Column(Integer, ForeignKey("cs_case.id"), nullable=False, index=True)
    transfer_no = Column(String, nullable=False, index=True)
    serial_no = Column(String, nullable=False, index=True)
    item_type = Column(String, nullable=False)  # set, ring, or cradle
    request = relationship("ReturnTransferRequest", back_populates="items")


class ReturnTransferHistory(Base):
    __tablename__ = "return_transfer_history"

    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("return_transfer_request.id"), nullable=False, index=True
    )
    status = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    request = relationship("ReturnTransferRequest", back_populates="history")