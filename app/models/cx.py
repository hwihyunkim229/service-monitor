from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class CXAnalysis(Base):
    __tablename__ = "cx_analysis"

    id = Column(Integer, primary_key=True)
    transfer_no = Column(
        String, ForeignKey("cs_case.transfer_no"), unique=True, index=True
    )
    result_summary = Column(String)
    data_loss_type = Column(String, nullable=True)
    data_loss_count = Column(Integer, nullable=True)
    cx_yn = Column(String)
    rnd_yn = Column(String)
    rnd_team = Column(String)
    received_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    issue1 = Column(String)
    depth1_1 = Column(String)
    depth1_2 = Column(String)
    issue2 = Column(String)
    depth2_1 = Column(String)
    depth2_2 = Column(String)
    exchange_type = Column(String)
    completed_at = Column(DateTime, default=datetime.utcnow)
    cs = relationship("CSCase", back_populates="cx", uselist=False)