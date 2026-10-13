QUALITY_COLUMNS = [
    ("current_self_cradle", "충전전류(Self Cradle)"),
    ("current_master_ring", "충전전류(Master Ring)"),
    ("current_master_cradle", "충전전류(Master Cradle)"),
    ("station_charge", "충전가능여부(Station)"),
    ("charge_log", "충전 후 상태(Log)"),
    ("battery_3h", "충전 중 배터리(3H)"),
    ("disconnect_connectable", "충전 해제 후 연결(20M)"),
    ("battery_after_disconnect", "충전 해제 후 배터리(20M)"),
    ("signal", "신호"),
    ("bt", "BT"),
    ("appearance", "외관"),
    ("discharge_time", "방전 시간"),
    ("discharge_result", "방전 후 상태"),
    ("water_damage", "침수"),
    ("cap_height", "캡높이"),
    ("defect_detail", "판정 불량 내용"),
    ("quality_result", "품질 판정"),
]

QUALITY_FIELDS = [field for field, _label in QUALITY_COLUMNS]
QUALITY_LABELS = dict(QUALITY_COLUMNS)
QUALITY_HEADER_FIELDS = {label: field for field, label in QUALITY_COLUMNS}