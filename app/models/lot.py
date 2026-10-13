from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from app.database import Base


class LotInventory(Base):
    __tablename__ = "lot_inventory"

    id = Column(Integer, primary_key=True)
    lot = Column(String, index=True)
    quantity = Column(Integer)
    week = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class LotCurrent(Base):
    __tablename__ = "lot_current"

    lot = Column(String, primary_key=True)
    quantity = Column(Integer)
    last_updated = Column(DateTime, default=datetime.utcnow)


class LotDefectManual(Base):
    __tablename__ = "lot_defect_manual"

    id = Column(Integer, primary_key=True)
    lot = Column(String, index=True)
    cs_count = Column(Integer)
    defect_count = Column(Integer)
    year = Column(Integer, default=2025)