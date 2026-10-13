from datetime import datetime
from sqlalchemy import Column, Date, DateTime, Integer, String
from app.database import Base


class DeviceWarranty(Base):
    __tablename__ = "device_warranty"

    id = Column(Integer, primary_key=True)
    root_serial_no = Column(String, unique=True, nullable=False, index=True)
    purchase_date = Column(Date, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)