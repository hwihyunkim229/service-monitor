from sqlalchemy import Column, Integer, String
from app.database import Base


class ExchangeInfo(Base):
    __tablename__ = "exchange_info"

    id = Column(Integer, primary_key=True)
    transfer_no = Column(String, index=True)
    serial_no = Column(String)
    size = Column(String)
    address = Column(String)
    contact = Column(String)
    name = Column(String)
    replacement_serial_no = Column(String)
    invoice_no = Column(String)