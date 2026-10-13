from sqlalchemy import Column, Integer, String, Date
from app.database import Base


class QualityInspection(Base):
    __tablename__ = "quality_inspection"

    id = Column(Integer, primary_key=True)
    transfer_no = Column(String, index=True)
    current_self_cradle = Column(Integer)
    current_master_ring = Column(Integer)
    current_master_cradle = Column(String)
    station_charge = Column(String)
    charge_log = Column(String)
    battery_3h = Column(Integer)
    disconnect_connectable = Column(String)
    battery_after_disconnect = Column(Integer)
    signal = Column(String)
    bt = Column(String)
    appearance = Column(String)
    discharge_time = Column(String)
    discharge_result = Column(String)
    water_damage = Column(String)
    cap_height = Column(String)
    defect_detail = Column(String)
    quality_result = Column(
        String, nullable=False, default="대기", server_default="대기"
    )
    lab_transfer_date = Column(Date, nullable=True)
    return_transfer_date = Column(Date, nullable=True)
    rev = Column(String, nullable=True)