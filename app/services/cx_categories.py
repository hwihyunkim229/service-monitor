CART_BP_PRO_CATEGORY_MAP = {
    "기기 연결 불가": ["기기 연결 불가", "일시적 기기 연결 불가 (CS 해결)"],
    "기기 고장": [
        "기기 상태 이상(배터리 성능)",
        "기기 상태 이상(PMIC OVP)",
        "기기 상태 이상(AFE 통신불량)",
        "기기 상태 이상(과열)",
        "기기 상태 이상(충전 중 same battery)",
        "발열",
        "센서 Red LED 깜빡임",
        "충전 불량",
        "전원 불량",
        "크래들 LED 안켜짐",
        "크래들 Green, Red 반복",
        "크래들 Red LED 깜빡임",
        "크래들 Green LED 깜빡임",
        "센서 습기",
        "기타",
    ],
    "S/W": [
        "앱 꺼짐",
        "무한 로딩",
        "타임 아웃",
        "환자 관련",
        "측정 관련",
        "로그인 실패",
        "기타",
    ],
    "기타": ["기기 도입", "데이터 및 사용 관련 문의", "회수 문의", "기타"],
    "측정 횟수": ["횟수 조정", "횟수 문의", "기타"],
    "리포트 이슈": ["리포트 미생성", "데이터 누락", "기타"],
    "측정 실패": ["최적화 실패", "보정 실패", "기타"],
    "환자 연결 해제": [],
    "고객 불만": ["단순", "강성 교품"],
    "정품 인증": ["정품 인증", "병원 이동 및 등록", "기타"],
}

CART_BP_CATEGORY_MAP = {
    "기기 연결 불가": ["기기 연결 불가", "일시적 기기 연결 불가 (CS 해결)"],
    "기기 고장": [
        "기기 상태 이상(배터리 성능)",
        "기기 상태 이상(PMIC OVP)",
        "기기 상태 이상(AFE 통신불량)",
        "기기 상태 이상(과열)",
        "기기 상태 이상(충전 중 same battery)",
        "기기 상태 이상(배터리 fault)",
        "발열",
        "센서 Red LED 깜빡임",
        "충전 불량",
        "전원 불량",
        "크래들 LED 안켜짐",
        "크래들 Green, Red 반복",
        "크래들 Red LED 깜빡임",
        "크래들 Green LED 깜빡임",
        "센서 습기",
        "기타",
    ],
    "S/W": ["앱 꺼짐", "무한 로딩", "타임 아웃", "측정 관련", "기타"],
    "기타": ["데이터 및 사용 관련 문의", "회수 문의", "기타"],
    "구매 문의": ["주문", "결제", "배송", "기타"],
    "데이터 이슈": ["데이터 누락", "기타"],
    "측정 실패": ["보정 실패", "기타"],
    "사용성": [],
    "제품 사후 관리": ["반품", "환불", "AS", "기타", "교환"],
    "고객 불만": ["단순", "강성 교품", "환불"],
    "정품 인증": ["정품 인증", "기타"],
}


def get_category_map(product):
    from app.services.products import product_profile

    return CART_BP_CATEGORY_MAP if product_profile(product) == "bp" else CART_BP_PRO_CATEGORY_MAP


def normalize_category(product, main, sub):
    from app.services.products import product_profile

    if product_profile(product) != "bp":
        return main, sub
    if main == "리포트 이슈":
        main = "데이터 이슈"
    available = CART_BP_CATEGORY_MAP.get(main)
    if available is None:
        return "기타", "기타"
    if sub and sub not in available:
        sub = "기타" if "기타" in available else ""
    return main, sub


def rev_from_serial(serial_no):
    serial = str(serial_no or "").strip().upper()
    if serial.startswith(("F", "K")):
        return "Rev.1"
    if serial.startswith("G"):
        return "Rev.2"
    return None