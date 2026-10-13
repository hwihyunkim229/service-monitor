import re
from datetime import datetime

from app.services.cx_categories import normalize_category
from app.services.products import is_bp_product

MESSAGE_START = re.compile(
    r"(?mi)^\s*(?:[-•]\s*)?(?:\*{0,2}\d+[.)]\s*)?"
    r"\*{0,2}이관\s*번호\*{0,2}\s*[:：]"
)

FIELD_PREFIX = r"(?:[-•]\s*)?(?:\*{0,2}\d+[.)]\s*)?\*{0,2}"
TRANSFER_LABEL = r"이관\s*번호"
DESCRIPTION_LABEL = r"(?:문의사항|불만사항)"
RECEIVED_LABEL = r"(?:접수시간|불만\s*현상\s*발생\s*시점)"


def _clean(value):
    return re.sub(r"[ \t\u00a0]+", " ", (value or "").strip())


def _clean_hospital_name(value):
    """Remove Daewoong sales-representative text wrapped around a hospital."""
    cleaned = _clean(value)
    match = re.fullmatch(r"대웅제약\s+.+?\s*\((.+)\)", cleaned)
    return _clean(match.group(1)) if match else cleaned


def _parse_device(value):
    raw = str(value or "")
    serial_match = re.search(r"\b[A-Z]\d{6,7}-[A-Z0-9]+\b", raw, re.IGNORECASE)
    if not serial_match:
        return "", ""
    serial_no = serial_match.group(0).upper()
    size_match = re.search(r"(?:/|-)\s*(\d+)\s*호", raw[serial_match.end() :])
    return serial_no, (size_match.group(1) + "호" if size_match else "")


def _parse_datetime(value):
    value = (
        re.sub(r"\s*\(편집됨\)\s*$", "", _clean(value))
        .replace("년 ", "-")
        .replace("월 ", "-")
        .replace("일 ", "")
    )
    value = value.replace("오전", "AM").replace("오후", "PM")
    timestamp = re.search(
        r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+(?:AM|PM)\s*)?\s+\d{1,2}:\d{2}(?::\d{2})?",
        value,
        re.IGNORECASE,
    )
    if timestamp:
        value = timestamp.group(0)
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m/%d %H:%M",
        "%m-%d %H:%M",
        "%Y-%m-%d %p %I:%M",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            return (
                parsed.replace(year=datetime.now().year)
                if fmt.startswith("%m")
                else parsed
            )
        except ValueError:
            continue
    return None


def _apply_slack_message_time(received_at, context):
    """Use a bracketed Slack header time while retaining the received date."""
    if received_at is None:
        return None
    matches = list(
        re.finditer(
            r"(?:\\?\[|【)[\s\u200b]*(오전|오후)[\s\u00a0\u200b]*(\d{1,2}):(\d{2})[\s\u200b]*(?:\\?\]|】)",
            context or "",
            re.IGNORECASE,
        )
    )
    if not matches:
        return received_at
    meridiem, hour_value, minute_value = matches[-1].groups()
    hour = int(hour_value) % 12
    if meridiem == "오후":
        hour += 12
    return received_at.replace(hour=hour, minute=int(minute_value), second=0)


def _device_statuses(value):
    """Read sensor and Bluetooth checks independently from Slack text."""
    text = re.sub(r"\s+", " ", value or "")
    sensor_match = re.search(
        r"(?:링\s*센서|센서|반지)\s*[:：]?\s*([OX])(?:\b|\s|[,/])",
        text,
        re.IGNORECASE,
    )
    bt_match = re.search(
        r"(?:BT\s*목록|BT)\s*[:：]?\s*([OX])(?:\b|\s|[,/])",
        text,
        re.IGNORECASE,
    )
    return (
        sensor_match.group(1).upper() if sensor_match else None,
        bt_match.group(1).upper() if bt_match else None,
    )


def _status_summary(value):
    sensor, bt = _device_statuses(value)
    if sensor == "X" and bt == "X":
        return "센서 및 BT 확인 불가"
    if sensor == "X" and bt == "O":
        return "센서 확인 불가, BT 확인 가능"
    if sensor == "O" and bt == "X":
        return "센서 확인 가능, BT 확인 불가"
    if sensor == "O" and bt == "O":
        return "센서 및 BT 확인 가능"
    if sensor == "X":
        return "센서 확인 불가"
    if sensor == "O":
        return "센서 확인 가능"
    if bt == "X":
        return "BT 확인 불가"
    if bt == "O":
        return "BT 확인 가능"
    return ""


def _first_keyword_position(value, keywords):
    positions = [value.find(keyword) for keyword in keywords if keyword in value]
    return min(positions) if positions else -1


def _cart_bp_unlabelled_description(block):
    """Extract free-form CART BP requests that omit the 문의사항 label."""
    transfer_line = re.search(
        rf"(?mi)^\s*{FIELD_PREFIX}이관번호\*{{0,2}}\s*[:：][^\n]*\n?",
        block,
    )
    if not transfer_line:
        return ""

    value = block[transfer_line.end() :]
    value = re.sub(
        rf"(?mi)^\s*{FIELD_PREFIX}(?:고객명|병원명)\*{{0,2}}\s*[:：][^\n]*\n?",
        "",
        value,
        count=1,
    )

    next_header = re.search(
        r"(?mi)^.*?(?:\[|【)\s*(?:오전|오후)\s*\d{1,2}:\d{2}\s*(?:\]|】).*?$",
        value,
    )
    if next_header:
        value = value[: next_header.start()]
    value = re.sub(r"(?mi)^\s*(?:image\.[a-z0-9]+|\(편집됨\))\s*$", "", value)
    value = re.sub(r"(?mi)^\s*@[^\n]*$", "", value)
    return value.strip()


def _connection_failure_result(value):
    status = _status_summary(value)
    data_transfer_failure = any(
        keyword in value
        for keyword in (
            "데이터 전송 지연",
            "데이터 전송 실패",
            "데이터 전송 불가",
            "데이터 전송 중 끊김",
        )
    )
    summary = (
        "데이터 전송 중 기기 연결 불가" if data_transfer_failure else "기기 연결 불가"
    )
    return (
        "기기 연결 불가",
        "기기 연결 불가",
        f"{summary}, {status}" if status else summary,
    )


def classify_cart_bp(description):
    """Classify consumer CART BP messages using the curated legacy workbook."""
    normalized = re.sub(r"\s+", " ", description or "").strip()
    lower = normalized.lower()

    def contains(*keywords):
        return any(keyword.lower() in lower for keyword in keywords)

    def first_position(*keywords):
        positions = [lower.find(keyword.lower()) for keyword in keywords]
        positions = [position for position in positions if position >= 0]
        return min(positions) if positions else None

    sensor_summary = _status_summary(normalized)
    candidates = []

    if contains("보정 실패"):
        return "측정 실패", "보정 실패", "보정 실패 사유 확인 요청"
    if contains(
        "환불 요청", "환불요청", "구매 취소", "구매취소", "취소 요청", "취소요청"
    ):
        if contains("불만", "미 배송", "미배송"):
            return "고객 불만", "환불", "구매 취소 및 환불 요청"
        return "제품 사후 관리", "환불", "환불 요청"
    if contains("반품 요청", "반품요청", "반품 후", "반품후", "고객 변심"):
        return "제품 사후 관리", "반품", "반품 요청"
    if contains("기기 수령 후", "회수 후 검수") and contains(
        "외관", "이음새", "파손", "불량 상태"
    ):
        return "제품 사후 관리", "AS", "AS 요청"

    if contains("방전 빠", "방전이 빠") and not contains("상태 이상 메시지"):
        return "기기 고장", "기타", "방전 빠름 문의"

    def add(main, sub, summary, *keywords, priority=50):
        position = first_position(*keywords)
        if position is not None:
            candidates.append((position, priority, main, sub, summary))

    missing_keywords = (
        "데이터 누락",
        "데이터 미노출",
        "데이터 미표기",
        "데이터 확인 불가",
        "혈압값 확인 불가",
        "혈압값 표시되지",
        "혈압 데이터 확인되지",
        "혈압 데이터 미노출",
        "혈압값 미노출",
        "맥박 미표기",
        "업로드 되지 않",
        "혈압 값이 보여지지",
        "혈압값이 보여지지",
        "데이터가 앱에 표기되지",
        "데이터가 보여지지",
        "데이터 값이 확인되지",
        "혈압 데이터가 확인되지",
        "데이터 확인되지",
        "혈압데이터 확인되지",
        "혈압 값 확인 불가",
        "혈압값 노출되지",
        "혈압 데이터 비노출",
        "혈압데이터 비노출",
        "혈압 값 확인 불가",
        "혈압데이터 확인되지",
        "데이터 미확인",
        "데이터 미 표시",
        "데이터 미표시",
    )
    if contains(*missing_keywords):
        summary = (
            "데이터 전송 지연 사유 확인 요청 및 누락 사유 확인 요청"
            if contains("전송 지연")
            else "데이터 누락 사유 확인 요청"
        )
        add("데이터 이슈", "데이터 누락", summary, *missing_keywords, priority=10)
    add(
        "데이터 이슈",
        "기타",
        "데이터 전송 지연 사유 확인 요청",
        "데이터 전송 지연",
        "데이터 업로드 지연",
        "전송 시 2~3시간 정도 지연",
        "데이터가 변경되어",
        priority=20,
    )
    add(
        "데이터 이슈",
        "기타",
        "측정 데이터 이상 여부 확인 요청",
        "측정값 차이",
        "혈압값 차이",
        "혈압 값 차이",
        "편차가",
        "낮게 나와",
        "높게 측정",
        "부정맥",
        "불규칙 맥파",
        "혈압 평균 미노출",
        "평균 값이 노출되지",
        "혈압값의 차이가",
        "데이터의 이상 여부",
        priority=25,
    )

    connection_summary = (
        f"기기 연결 불가, {sensor_summary}" if sensor_summary else "기기 연결 불가"
    )
    add(
        "기기 연결 불가",
        "기기 연결 불가",
        connection_summary,
        "기기 연결 불가",
        "기기연결 불가",
        "카트링 연결 불가",
        "카트링 등록 불가",
        "기기 등록 불가",
        "재등록 불가",
        "블루투스 검색 불가",
        "기기번호 비노출",
        "탐색 중",
        "탐색중",
        "연결 끊김",
        "연결이 끊",
        "연결이 자주 끊",
        "카트링 확인 및 연결 불가",
        priority=15,
    )

    add("기기 고장", "센서 습기", "센서 습기", "센서 습기", "링 센서 습기", priority=10)
    add(
        "기기 고장",
        "센서 Red LED 깜빡임",
        "충전 중 센서 Red LED 깜빡임",
        "센서 red led",
        "센서 점멸",
        "센서 깜빡임",
        "센서 점등",
        priority=20,
    )
    add(
        "기기 고장",
        "크래들 Green, Red 반복",
        "충전 중 Red > Green 반복",
        "green > red",
        "red > green",
        "green, red 반복",
        "초록불에서 빨간불",
        priority=10,
    )
    cradle_status = (
        f"크래들 미점등, {sensor_summary}" if sensor_summary else "크래들 미점등"
    )
    add(
        "기기 고장",
        "크래들 LED 안켜짐",
        cradle_status,
        "크래들 미점등",
        "크래들 led 안",
        "크래들 불이 안",
        priority=20,
    )
    add(
        "기기 고장",
        "크래들 Red LED 깜빡임",
        "크래들 깜빡임",
        "크래들 red led 깜빡",
        "크래들 빨간불 깜빡",
        "크래들 깜빡임",
        priority=25,
    )
    add(
        "기기 고장",
        "크래들 Green LED 깜빡임",
        "충전 중 크래들 Green LED 깜빡임",
        "크래들 green led 깜빡",
        "크래들 초록불 깜빡",
        priority=25,
    )
    add(
        "기기 고장",
        "충전 불량",
        "완충 불가",
        "완충 불가",
        "충전 불량",
        "green 전환 x",
        "초록불로 전환이 안",
        priority=15,
    )
    add(
        "기기 고장",
        "기기 상태 이상(배터리 성능)",
        "기기 상태 불량",
        "배터리 성능",
        "배터리 불량",
        "배터리 소모",
        "방전 빠",
        "방전이 빠",
        "배터리 방전",
        "배터리가 0%",
        priority=20,
    )
    add(
        "기기 고장",
        "기기 상태 이상(PMIC OVP)",
        "기기 상태 불량",
        "카트링 상태 이상",
        "기기 상태 이상 메시지",
        "pmic",
        priority=20,
    )
    add(
        "기기 고장",
        "기기 상태 이상(AFE 통신불량)",
        "기기 상태 불량",
        "afe",
        "센서 불량",
        priority=25,
    )
    add(
        "기기 고장", "발열", "발열로 인한 기기 이상 여부 확인 요청", "발열", priority=20
    )
    add(
        "기기 고장",
        "기타",
        "외관 불량으로 확인 요청",
        "외관 불량",
        "기스",
        "스크래치",
        "마모",
        "코팅이 벗겨",
        "이음새",
        "파손",
        priority=35,
    )

    add("S/W", "무한 로딩", "무한 로딩 발생", "무한 로딩", "무한로딩", priority=10)
    add(
        "S/W",
        "앱 꺼짐",
        "앱 종료 현상 확인 요청",
        "앱 꺼짐",
        "앱 강제 종료",
        priority=20,
    )
    add("S/W", "타임 아웃", "타임 아웃 발생", "타임 아웃", "타임아웃", priority=20)
    add(
        "S/W",
        "기타",
        "DFU 실패 문의",
        "dfu",
        "펌웨어 업데이트",
        "카트링 업데이트",
        "업데이트 실패",
        "업데이트 불가",
        priority=25,
    )
    add(
        "S/W",
        "기타",
        "앱 내 배터리 표시 오류 확인 요청",
        "배터리 표시 오류",
        "배터리 잔량이 줄어들지",
        "배터리가 100%에서",
        priority=20,
    )

    add("측정 실패", "보정 실패", "보정 실패 사유 확인 요청", "보정 실패", priority=10)
    add(
        "정품 인증",
        "정품 인증",
        "기기 등록 시 정품 확인 불가",
        "정품 인증",
        "정품 확인 불가",
        "다른 사용자에게 등록",
        priority=15,
    )

    add("제품 사후 관리", "AS", "AS 요청", "as 요청", "a/s 요청", priority=10)
    add(
        "제품 사후 관리",
        "교환",
        "교환 요청",
        "사이즈 교환",
        "사이즈 변경",
        "오배송",
        "교환 요청",
        priority=15,
    )
    add(
        "제품 사후 관리",
        "환불",
        "환불 요청",
        "환불 요청",
        "환불요청",
        "환불 접수",
        priority=15,
    )
    add(
        "제품 사후 관리",
        "반품",
        "반품 요청",
        "반품 요청",
        "반품요청",
        "반품 접수",
        "단순 변심 반품",
        "주문 취소",
        priority=20,
    )

    add("구매 문의", "배송", "배송 문의", "배송 문의", "주소지", "배송지", priority=20)
    add("구매 문의", "주문", "주문 문의", "주문 문의", "구매 방법", priority=20)
    add(
        "구매 문의",
        "기타",
        "크래들 추가 구매 가능 여부 문의",
        "크래들 추가 구매",
        "크래들 별도 구매",
        priority=15,
    )

    add(
        "고객 불만",
        "강성 교품",
        "강성 교품 요청",
        "강성 교품",
        "강성 항의",
        priority=10,
    )
    add(
        "고객 불만",
        "단순",
        "고객 불만 접수",
        "소비자원",
        "불만",
        "어댑터를 배송",
        priority=40,
    )

    add("기타", "회수 문의", "회수 문의", "회수 문의", "회수 접수", priority=20)
    add(
        "기타",
        "데이터 및 사용 관련 문의",
        normalized,
        "사용 가능 여부",
        "사용 가능한가",
        "해외에서 사용",
        "해외 사용",
        "로밍",
        "앱 설치",
        "회원 가입",
        "비밀번호",
        "계정 삭제",
        priority=30,
    )
    add(
        "기타",
        "기타",
        "CART BP 앱에 연결된 기기 삭제 요청",
        "기기 삭제 요청",
        "기기 번호 삭제",
        "카트링 직접 삭제",
        priority=20,
    )

    if not candidates:
        return "기타", "기타", normalized
    _position, _priority, main, sub, summary = min(
        candidates, key=lambda item: (item[0], item[1])
    )
    return main, sub, summary


def classify_and_summarize(description):
    normalized = re.sub(r"\s+", " ", description).strip()
    lower = normalized.lower()
    connection_keywords = (
        "기기 연결 불가",
        "카트링 연결 불가",
        "카트링 확인 불가",
        "근접 카트링 목록에 기기번호 비노출",
        "링 센서 X, BT 목록 X",
        "데이터 전송 지연",
        "데이터 전송 실패",
        "데이터 전송 불가",
        "데이터 전송 중 끊김",
    )
    cradle_keywords = (
        "크래들 미점등",
        "led 안켜",
        "led 미점등",
        "크래들 red led 점멸",
        "크래들 red led",
        "크래들 빨간불 깜빡",
        "크래들 깜빡",
    )
    connection_position = _first_keyword_position(normalized, connection_keywords)
    cradle_position = _first_keyword_position(lower, cradle_keywords)
    if any(
        word in normalized
        for word in (
            "다운로드 할 데이터가 없습니다",
            "다운로드할 데이터가 없습니다",
            "리포트 다운로드 실패",
            "리포트 미생성",
            "리포트 출력 시 미생성",
            "미생성",
        )
    ):
        return "리포트 이슈", "리포트 미생성", "데이터 전송 후 다운로드 할 데이터 없음"
    if any(
        word in lower
        for word in (
            "데이터 누락",
            "리포트 누락",
            "bp unavailable",
            "수율",
            "데이터 분석 요청",
            "리포트 내용 이상",
            "측정 누락",
            "측정이 안됨",
        )
    ):
        return "리포트 이슈", "데이터 누락", "데이터 누락 사유 확인 요청"
    if "보정 실패" in normalized:
        return "측정 실패", "보정 실패", "보정 실패 사유 확인 요청"
    if any(
        word in normalized
        for word in ("횟수 원복", "횟수 복원", "횟수 조정", "원복 요청")
    ):
        if any(word in normalized for word in ("오연결", "오 연결")):
            summary = "환자 오연결로 인한 원복 요청"
        elif any(word in normalized for word in ("변심", "손가락 변경")):
            summary = "환자 단순 변심으로 인한 원복 요청"
        elif "사이즈" in normalized:
            summary = "사이즈 변경으로 인한 원복 요청"
        else:
            summary = "기기 고장으로 인한 원복 요청"
        return "측정 횟수", "횟수 조정", summary

    if any(
        word in normalized
        for word in ("완충 불가", "녹색으로 미전환", "GREEN LED로 미전환")
    ):
        return "기기 고장", "충전 불량", "충전 불량 문의"
    if any(
        word in normalized
        for word in (
            "정품 확인 불가",
            "정품인증 불가",
            "정품 인증 실패",
            "정품인증 실패",
            "정품 인증",
            "타병원",
            "병원 이동",
        )
    ):
        return "정품 인증", "병원 이동 및 등록", "기기 등록 시 정품 확인 불가 팝업"
    if any(
        word in normalized
        for word in ("환자 연결 해제", "기기 연결 해제", "연결 해제", "기기 분실")
    ):
        return "환자 연결 해제", "", "환자 연결 해제 요청"
    if connection_position >= 0 and (
        cradle_position < 0 or connection_position < cradle_position
    ):
        return _connection_failure_result(normalized)
    if any(word in normalized for word in ("센서 습기", "링 센서 습기", "침수")):
        return "기기 고장", "센서 습기", "센서 습기"
    if any(
        word in lower
        for word in (
            "센서 red led",
            "링 red led",
            "센서 빨간",
            "카트링 센서 점멸",
            "센서 점멸",
        )
    ):
        return "기기 고장", "센서 Red LED 깜빡임", "충전 중 센서 Red led 점멸"
    if any(
        word in lower
        for word in (
            "크래들 red led 점멸",
            "크래들 red led 깜빡",
            "크래들 빨간불 깜빡",
            "크래들 깜빡",
        )
    ) or (
        "크래들" in normalized
        and "깜빡" in normalized
        and any(word in lower for word in ("램프", "led"))
    ):
        status = _status_summary(normalized)
        return (
            "기기 고장",
            "크래들 Red LED 깜빡임",
            f"크래들 깜빡임, {status}" if status else "크래들 깜빡임",
        )
    if any(
        word in lower
        for word in (
            "크래들 green led 점멸",
            "크래들 green led 깜빡",
            "green led 깜빡",
        )
    ):
        return "기기 고장", "크래들 Green LED 깜빡임", "충전 중 Cradle Green LED 점멸"
    if any(word in normalized for word in ("크래들 미점등", "LED 안켜", "LED 미점등")):
        status = _status_summary(normalized)
        return (
            "기기 고장",
            "크래들 LED 안켜짐",
            f"크래들 미점등, {status}" if status else "크래들 미점등",
        )
    if connection_position >= 0:
        return _connection_failure_result(normalized)
    if any(word in normalized for word in ("방전 빠름", "방전이 빠", "방전 속도")):
        return "기기 고장", "기타", "방전 빠름 문의"
    if "발열" in normalized:
        return "기기 고장", "발열", "발열 문의"
    if any(
        word in normalized
        for word in ("배터리 시간 불량", "배터리 성능", "배터리 지속")
    ):
        return "기기 고장", "기기 상태 이상(배터리 성능)", "기기 상태 불량"
    if any(word in normalized for word in ("카트링 상태 이상", "배터리 불량", "PMIC")):
        return "기기 고장", "기기 상태 이상(PMIC OVP)", "기기 상태 불량"
    if "충전 불량" in normalized:
        return "기기 고장", "충전 불량", "충전 불량 문의"
    if any(word in normalized for word in ("외관 불량", "찌그러짐", "파손")):
        return "기기 고장", "기타", "외관 불량 문의"

    if "회수" in normalized:
        return (
            "기타",
            "회수 문의",
            (
                "오회수 문의"
                if "오회수" in normalized or "오 회수" in normalized
                else "회수 문의"
            ),
        )
    if any(
        word in normalized
        for word in (
            "팍스",
            "다이콤",
            "사용 가능 여부",
            "사용 관련 문의",
            "기기 점검 요청",
        )
    ):
        return "기타", "데이터 및 사용 관련 문의", normalized
    lines = [
        line.strip(" -/") for line in description.splitlines() if line.strip(" -/")
    ]
    return "기타", "기타", normalized or (lines[0] if lines else "")


def parse_slack_cs_messages(text, product="cart_bp_pro"):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")

    text = re.sub(
        r"(?<!\n)(?=(?:\*{0,2}[1-5][.)]\s*\*{0,2}(?:"
        r"이관\s*번호|병원명|고객명|(?:Serial\s*No\.?|S\s*/?\s*N|시리얼(?:\s*번호)?)"
        r"\s*/\s*Size|문의사항|불만사항|접수시간|불만\s*현상\s*발생\s*시점)\*{0,2}\s*[:：]))",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    bp_profile = is_bp_product(product)
    drafts = []
    starts = list(MESSAGE_START.finditer(text))
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[start.start() : end]
        context_start = starts[index - 1].start() if index else 0
        mention_context = text[context_start : start.start()]

        slack_headers = list(
            re.finditer(
                r"(?mi)^.*?(?:\[|【)\s*(?:오전|오후)\s*\d{1,2}:\d{2}\s*(?:\]|】).*?$",
                mention_context,
            )
        )
        if slack_headers:
            mention_context = mention_context[slack_headers[-1].start() :]

        def field(label):
            found = re.search(
                rf"(?mi)^\s*{FIELD_PREFIX}(?:{label})\*{{0,2}}\s*[:：]\s*\*{{0,2}}([^\n*]+)",
                block,
            )
            return found.group(1).strip().rstrip("*").rstrip() if found else ""

        def field_until_label(label, next_label):
            found = re.search(
                rf"(?mis)^\s*{FIELD_PREFIX}(?:{label})\*{{0,2}}\s*[:：]\s*\*{{0,2}}(.*?)\s*"
                rf"(?=^\s*{FIELD_PREFIX}(?:{next_label})\*{{0,2}}\s*[:：])",
                block,
            )
            return found.group(1).strip().rstrip("*").rstrip() if found else ""

        transfer_no = field(TRANSFER_LABEL)
        hospital_name = field_until_label(
            "(?:병원명|고객명)",
            r"(?:Serial\s*No\.?|S\s*/?\s*N|시리얼(?:\s*번호)?)\s*/\s*Size",
        )
        device = field_until_label(
            r"(?:Serial\s*No\.?|S\s*/?\s*N|시리얼(?:\s*번호)?)\s*/\s*Size",
            DESCRIPTION_LABEL,
        )
        description_match = re.search(
            rf"(?mis)^\s*{FIELD_PREFIX}{DESCRIPTION_LABEL}\*{{0,2}}\s*[:：]\s*\*{{0,2}}(.*?)\s*"
            rf"(?=^\s*{FIELD_PREFIX}{RECEIVED_LABEL}\*{{0,2}}\s*[:：]|\Z)",
            block,
        )
        description = (
            description_match.group(1).strip()
            if description_match
            else _cart_bp_unlabelled_description(block)
            if bp_profile
            else ""
        )
        received_value = field(RECEIVED_LABEL)
        required_values = (transfer_no, description)
        if not bp_profile:
            required_values += (received_value,)
        if not all(required_values) or (not bp_profile and not hospital_name):
            continue
        serial_no, size = _parse_device(device)
        received_at = _apply_slack_message_time(
            _parse_datetime(received_value), mention_context
        )
        if bp_profile:
            category_main, category_sub, summary = classify_cart_bp(description)
        else:
            category_main, category_sub, summary = classify_and_summarize(description)
        category_main, category_sub = normalize_category(
            product, category_main, category_sub
        )
        draft = {
            "transfer_no": _clean(transfer_no),
            "cx_transfer": (
                "O"
                if any(
                    mention in mention_context.lower()
                    for mention in ("@hwihyun.kim", "@wondong.yang")
                )
                else ""
            ),
            "category_main": category_main,
            "category_sub": category_sub,
            "description": summary,
            "received_at": (
                received_at.isoformat(timespec="seconds") if received_at else ""
            ),
            "serial_no": serial_no,
            "lot": serial_no.split("-", 1)[0] if "-" in serial_no else "",
            "size": size,
            "raw_description": description,
        }
        if not bp_profile:
            draft["hospital_name"] = _clean_hospital_name(hospital_name)
        drafts.append(draft)
    return drafts