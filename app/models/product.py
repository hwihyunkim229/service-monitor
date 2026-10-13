from sqlalchemy import Column, Integer, String
from app.database import Base


class OperatingProduct(Base):
    __tablename__ = "operating_product"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    profile = Column(String, nullable=False, default="pro")
    jira_project = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=100)