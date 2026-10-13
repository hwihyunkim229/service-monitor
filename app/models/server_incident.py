from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text, Index
from app.database import Base


class ServerIncident(Base):
    """One manually reported server interruption and its business impact."""

    __tablename__ = "server_incident"
    __table_args__ = (Index("ix_server_incident_product_occurred", "product", "occurred_at"),)

    id = Column(Integer, primary_key=True)
    product = Column(String, nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=0)
    reason = Column(Text)
    cs_count = Column(Integer, nullable=False, default=0)
    cs_details = Column(Text)
    rnd_status = Column(String, nullable=False, default="대응 예정")
    rnd_response = Column(Text)
    business_status = Column(String, nullable=False, default="대응 예정")
    business_response = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)