import asyncio

from app.models.cs import CSCase
from app.routers import renewal
from app.services.cx_categories import (
    CART_BP_CATEGORY_MAP,
    CART_BP_PRO_CATEGORY_MAP,
    get_category_map,
    normalize_category,
    rev_from_serial,
)
from app.services.slack_cs_parser import parse_slack_cs_messages

BP_SAMPLE = """메타엠_신유정 [오전 9:08]
@hwihyun.kim
1. 이관번호 : 9993
2. 병원명 : 테스트병원
3. Serial No. / Size : K253319-DD1C1 / 12호
4. 문의사항 : 데이터 누락 확인 요청
5. 접수시간 : 2026-08-25 09:08:00"""


def bp_message(code, description, device="G261213-AAAA1 / 11호"):
    return f"""메타엠_신유정 [오전 10:29]
@wondong.yang @Taijin.you
1. 이관번호 : {code}
2. 고객명 : 최*호
3. Serial No. / Size : {device}
4. 문의사항 : {description}
5. 접수시간 : 2026-07-31 10:20:00"""


def test_cart_bp_uses_its_own_category_map():
    category_map = get_category_map("cart_bp")

    assert category_map is CART_BP_CATEGORY_MAP
    assert list(category_map) == [
        "기기 연결 불가",
        "기기 고장",
        "S/W",
        "기타",
        "구매 문의",
        "데이터 이슈",
        "측정 실패",
        "사용성",
        "제품 사후 관리",
        "고객 불만",
        "정품 인증",
    ]
    assert "기기 상태 이상(ECG)" in category_map["기기 고장"]
    assert category_map["제품 사후 관리"] == ["반품", "환불", "AS", "기타", "교환"]


def test_cart_bp_pro_keeps_existing_category_map():
    category_map = get_category_map("cart_bp_pro")

    assert category_map is CART_BP_PRO_CATEGORY_MAP
    assert "측정 횟수" in category_map
    assert "구매 문의" not in category_map


def test_cart_bp_slack_category_is_normalized_to_bp_options():
    item = parse_slack_cs_messages(BP_SAMPLE, "cart_bp")[0]

    assert item["category_main"] == "데이터 이슈"
    assert item["category_sub"] == "데이터 누락"
    assert "hospital_name" not in item
    assert "exchange_type" not in item


def test_cart_bp_slack_does_not_require_hospital_name():
    item = parse_slack_cs_messages(
        BP_SAMPLE.replace("2. 병원명 : 테스트병원\n", ""), "cart_bp"
    )[0]

    assert item["transfer_no"] == "9993"


def test_cart_bp_accepts_customer_name_and_uses_bp_specific_rules():
    messages = "\n\n".join(
        [
            bp_message("5604", "혈압 데이터 미노출 및 지연 문의"),
            bp_message("5606", "QR 코드 인식 후 카트링 등록 불가"),
            bp_message("9018", "앱 내 배터리 표시 오류로 잔량이 줄어들지 않음"),
            bp_message("9104", "완충 불가 및 카트링 연결 불가, 링 센서 X, BT X"),
        ]
    )
    items = parse_slack_cs_messages(messages, "cart_bp")

    assert [(item["category_main"], item["category_sub"]) for item in items] == [
        ("데이터 이슈", "데이터 누락"),
        ("기기 연결 불가", "기기 연결 불가"),
        ("S/W", "기타"),
        ("기기 고장", "충전 불량"),
    ]
    assert all("hospital_name" not in item for item in items)
    assert all(item["cx_transfer"] == "O" for item in items)


def test_cart_bp_allows_missing_device_field():
    item = parse_slack_cs_messages(
        bp_message("9995", "본품 출고 전 취소 및 반품 요청", device=""),
        "cart_bp",
    )[0]

    assert item["category_main"] == "제품 사후 관리"
    assert item["category_sub"] == "반품"


def test_cart_bp_decisive_phrases_override_incidental_words():
    messages = "\n\n".join(
        [
            bp_message("9996", "불규칙 맥파 안내 후 재시도했으나 보정 실패"),
            bp_message("9997", "배송 지연 불만으로 구매 취소요청"),
            bp_message("9998", "완충 후 다음날 방전 빠름 문의"),
            bp_message("9999", "카트링 연결이 자주 끊김"),
        ]
    )

    items = parse_slack_cs_messages(messages, "cart_bp")

    assert [(item["category_main"], item["category_sub"]) for item in items] == [
        ("측정 실패", "보정 실패"),
        ("고객 불만", "환불"),
        ("기기 고장", "기타"),
        ("기기 연결 불가", "기기 연결 불가"),
    ]


def test_cart_bp_unknown_subcategory_falls_back_to_other():
    assert normalize_category("cart_bp", "S/W", "로그인 실패") == ("S/W", "기타")


def test_k_serial_uses_rev_1():
    assert rev_from_serial("K253319-DD1C1") == "Rev.1"
    assert rev_from_serial("k253319-dd1c1") == "Rev.1"
    assert rev_from_serial("F253319-DD1C1") == "Rev.1"
    assert rev_from_serial("G253319-DD1C1") == "Rev.2"


def test_slack_import_ignores_hospital_and_exchange_values_for_cart_bp():
    class EmptyQuery:
        def filter(self, *args):
            return self

        def first(self):
            return None

    class MemorySession:
        def __init__(self):
            self.added = []

        def query(self, *args):
            return EmptyQuery()

        def add(self, item):
            self.added.append(item)

        def commit(self):
            pass

        def close(self):
            pass

    session = MemorySession()
    original_session_local = renewal.SessionLocal
    renewal.SessionLocal = lambda: session
    try:
        asyncio.run(
            renewal.slack_import(
                "cart_bp",
                {
                    "items": [
                        {
                            "transfer_no": "9994",
                            "hospital_name": "저장되면 안 되는 병원명",
                            "exchange_type": "X",
                            "received_at": "2026-08-25T09:08:00",
                            "serial_no": "K253319-DD1C1",
                        }
                    ]
                },
            )
        )
    finally:
        renewal.SessionLocal = original_session_local

    case = next(item for item in session.added if isinstance(item, CSCase))
    assert case.hospital_name is None
    assert case.exchange_type is None