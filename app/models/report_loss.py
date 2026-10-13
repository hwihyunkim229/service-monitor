from sqlalchemy import Column, Integer
from app.database import Base


class ReportLoss(Base):
    __tablename__ = "report_loss"

    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False)
    lost_include = Column(Integer, default=0)
    lost_exclude = Column(Integer, default=0)