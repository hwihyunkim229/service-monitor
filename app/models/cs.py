from sqlalchemy import Boolean, Column, Integer, String, DateTime, Index, event, select, func
from app.database import Base
from sqlalchemy.orm import relationship
from app.models.cx import CXAnalysis


class CSCase(Base):
    __tablename__ = "cs_case"
    __table_args__ = (
        Index(
            "ix_cs_case_product_exchange_received_at",
            "product",
            "exchange_type",
            "received_at",
        ),
    )

    id = Column(Integer, primary_key=True)
    product = Column(String, index=True, default="cart_bp_pro")
    date_only = Column(DateTime, nullable=True)
    received_at = Column(DateTime, index=True)
    processed_at = Column(DateTime, nullable=True)
    cs_started_at = Column(DateTime, nullable=True)
    cs_completed_at = Column(DateTime, nullable=True)
    week = Column(Integer, index=True)
    transfer_no = Column(String, index=True)
    hospital_name = Column(String)
    rev = Column(String)
    seller = Column(String)
    cx_transfer = Column(String)
    rnd_transfer = Column(String)
    handover_type = Column(String)
    rnd_team = Column(String, index=True)
    category_main = Column(String, index=True)
    category_sub = Column(String, index=True)
    description = Column(String)
    exchange_type = Column(String, index=True)
    serial_no = Column(String, index=True)
    lot = Column(String, index=True)
    size = Column(String)
    return_yn = Column(String, default="X")
    return_at = Column(DateTime, nullable=True)
    return_at_manual = Column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    invoice_no = Column(String, nullable=True)
    replacement_serial_no = Column(String, nullable=True)
    replacement_serial_no_manual = Column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    cx = relationship(CXAnalysis, back_populates="cs", uselist=False)

    @property
    def lead_time(self):
        if self.cx_transfer and self.received_at and self.processed_at:
            diff = (self.processed_at - self.received_at).total_seconds()
            if diff >= 0:
                h = int(diff // 3600)
                m = int((diff % 3600) // 60)
                s = int(diff % 60)
                return f"{h:02d}:{m:02d}:{s:02d}"
        return "-"


@event.listens_for(CSCase, "before_insert")
@event.listens_for(CSCase, "before_update")
def populate_lot_from_serial(_mapper, _connection, target):
    """Keep LOT populated when a write path provides only the serial number."""
    serial_no = str(target.serial_no or "").strip().upper()
    if serial_no and not str(target.lot or "").strip():
        target.lot = serial_no.split("-", 1)[0]

    if not serial_no or not str(target.product or "").strip():
        return

    table = CSCase.__table__
    query = select(table.c.hospital_name, table.c.size).where(
        table.c.product == target.product,
        func.upper(func.trim(table.c.serial_no)) == serial_no,
    )
    if target.id is not None:
        query = query.where(table.c.id != target.id)
    existing_rows = _connection.execute(query).all()

    def clean(value):
        text = str(value or "").strip()
        return "" if text.upper() in {"", "-", "N/A", "NONE", "NULL", "NAN"} else text

    def clean_size(value):
        text = clean(value)
        if text.endswith("호"):
            text = text[:-1].strip()
        if text.endswith(".0") and text[:-2].isdigit():
            text = text[:-2]
        return text

    if not clean(target.hospital_name):
        hospitals = {
            value
            for hospital, _size in existing_rows
            if (value := clean(hospital)) and value != "병원 미등록"
        }
        if len(hospitals) == 1:
            target.hospital_name = next(iter(hospitals))

    if not clean_size(target.size):
        sizes = {
            value
            for _hospital, size in existing_rows
            if (value := clean_size(size))
        }
        if len(sizes) == 1:
            target.size = next(iter(sizes))