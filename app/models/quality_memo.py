from sqlalchemy import Column, Integer, String, Text, ForeignKey
from app.database import Base


class QualityCellMemo(Base):
    __tablename__ = "quality_cell_memo"

    id = Column(Integer, primary_key=True, index=True)
    quality_id = Column(Integer, ForeignKey("quality_inspection.id"))
    field_name = Column(String, nullable=False)
    memo = Column(Text, nullable=True)