from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, String, Text
from app.database import Base


class ServerDowntime(Base):
    __tablename__ = "server_downtime"

    id = Column(Integer, primary_key=True)
    product = Column(String)
    year = Column(Integer)
    week = Column(Integer)
    downtime_minutes = Column(Integer)
    reason = Column(Text)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)