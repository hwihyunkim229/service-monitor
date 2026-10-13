from sqlalchemy import Column, Integer, String, Date
from app.database import Base


class LabAnalysis(Base):
    __tablename__ = "lab_analysis"

    id = Column(Integer, primary_key=True)
    transfer_no = Column(String, index=True)
    serial_no = Column(String)
    root_cause = Column(String)
    analysis_result = Column(String)
    defect_type = Column(String)
    remark = Column(String)
    analysis_completed_date = Column(Date)
    attachment = Column(String, default="[]")