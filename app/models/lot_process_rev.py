from datetime import datetime
from sqlalchemy import Column, DateTime, String
from app.database import Base


class LotProcessRev(Base):
    __tablename__ = "lot_process_rev"

    lot = Column(String, primary_key=True)
    process_rev = Column(String, nullable=False)
    ring_rev = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)