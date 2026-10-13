from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class RndTransferRequest(Base):
    __tablename__ = "rnd_transfer_request"

    id = Column(Integer, primary_key=True)
    product = Column(String, nullable=False, index=True)
    request_type = Column(String, nullable=False, default="set")
    urgent = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="requested", index=True)
    rejection_reason = Column(Text, nullable=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    items = relationship(
        "RndTransferItem", back_populates="request", cascade="all, delete-orphan"
    )
    history = relationship(
        "RndTransferHistory", back_populates="request", cascade="all, delete-orphan"
    )


class RndTransferItem(Base):
    __tablename__ = "rnd_transfer_item"

    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("rnd_transfer_request.id"), nullable=False, index=True
    )
    cs_id = Column(Integer, ForeignKey("cs_case.id"), nullable=False, index=True)
    transfer_no = Column(String, nullable=False, index=True)
    serial_no = Column(String, nullable=False, index=True)
    request = relationship("RndTransferRequest", back_populates="items")


class RndTransferHistory(Base):
    __tablename__ = "rnd_transfer_history"

    id = Column(Integer, primary_key=True)
    request_id = Column(
        Integer, ForeignKey("rnd_transfer_request.id"), nullable=False, index=True
    )
    status = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    request = relationship("RndTransferRequest", back_populates="history")