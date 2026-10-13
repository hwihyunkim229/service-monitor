from app.services.slack_cs_parser import parse_slack_cs_messages

SAMPLE = """메타엠_박상윤  [오후 3:27]
@hwihyun.kim@seokman.yun
1. 이관번호 : 9191
2. 병원명 : 신촌세브란스
3. Serial No. / Size : F252415-53C61 / 12호 / 환자번호 7006최병128
4. 문의사항 : 윤팀장님 슬랙접수건
리포트 출력 시 수율 41%밖에 측정 되지 않는 사유 확인 요청
5. 접수시간 : 2026-08-06 15:26:59 (편집됨)

메타엠_신유정  [오후 5:00]
@hwihyun.kim @메타엠_김형준
1. 이관번호 : 9192
2. 병원명 : 이른비한방병원(전주)
3. Serial No. / Size : G261216-08971 / 12호
4. 문의사항 : 크래들 미점등 / 충전 불량 및 기기 이상 여부 확인요청 / 지난주부터 증상 발현
기기 전원 확인 - 링 센서 X , BT 목록 X
5. 접수시간 : 2026-08-06 16:57:06 (편집됨)"""


def test_parse_example_messages():
    items = parse_slack_cs_messages(SAMPLE)
    assert len(items) == 2
    assert items[0]["cx_transfer"] == "O"
    assert items[0]["category_main"] == "리포트 이슈"
    assert items[0]["description"] == "데이터 누락 사유 확인 요청"
    assert items[1]["category_sub"] == "크래들 LED 안켜짐"
    assert items[1]["lot"] == "G261216"


def test_each_bracketed_slack_time_stays_with_its_own_case():
    sample = """메타엠_박상윤 [오전 9:23]
오전 11:11 안내 문구
1. 이관번호 : 9901
2. 병원명 : 첫 번째 병원
3. Serial No. / Size : F252415-11111 / 10호
4. 문의사항 : 데이터 누락 확인 요청
5. 접수시간 : 2026-09-18 17:40:00

메타엠_신유정 [오후 1:07]
1. 이관번호 : 9902
2. 병원명 : 두 번째 병원
3. Serial No. / Size : G261216-22221 / 12호
4. 문의사항 : 크래들 미점등 확인 요청
5. 접수시간 : 2026-09-18 09:00:00"""

    items = parse_slack_cs_messages(sample)

    assert [item["transfer_no"] for item in items] == ["9901", "9902"]
    assert items[0]["received_at"] == "2026-09-18T09:23:00"
    assert items[1]["received_at"] == "2026-09-18T13:07:00"


def test_unbracketed_meridiem_time_does_not_override_received_time():
    sample = """메타엠_박상윤
오전 11:11 안내 문구
1. 이관번호 : 9903
2. 병원명 : 세 번째 병원
3. Serial No. / Size : F252415-33331 / 10호
4. 문의사항 : 데이터 누락 확인 요청
5. 접수시간 : 2026-09-18 17:40:00"""

    item = parse_slack_cs_messages(sample)[0]

    assert item["received_at"] == "2026-09-18T17:40:00"


def test_markdown_slack_copy_uses_header_time_for_each_transfer():
    sample = """메타엠_박상윤\u00a0\u00a0[오전 9:23]\\
[@hwihyun.kim](https://i-skylabs.slack.com/team/U07KG6S6FMY) [@minseob.song](https://i-skylabs.slack.com/team/U07K1KTNP8F)
1. 이관번호 : 10039
2. 병원명 : 클리오닉이비인후과의원
3. Serial No. / Size : F251121-6DD51 / 9호
4. 문의사항 : 데이터 전송시 기기 연결 불가
5. 접수시간 : 2026-09-28 09:11:18
메타엠_박상윤\u00a0\u00a0[오전 9:58]\\
[**@minseob.song**](https://i-skylabs.slack.com/team/U07K1KTNP8F)
1. 이관번호 : 10042
2. 병원명 : 강남세브란스
3. Serial No. / Size : F243801-36361 / 11호
4. 문의사항 : 카트링 상태 이상 메시지 / 배터리 불량 교품
5. 접수시간 :\u00a0 2026-09-28 10:00:26\u00a0(편집됨)"""

    items = parse_slack_cs_messages(sample)

    assert [item["transfer_no"] for item in items] == ["10039", "10042"]
    assert items[0]["received_at"] == "2026-09-28T09:23:00"
    assert items[1]["received_at"] == "2026-09-28T09:58:00"


def test_collapsed_markdown_fields_and_headers_keep_their_own_times():
    sample = """메타엠_박상윤\u00a0\u00a0[오전 9:23]\\
[@hwihyun.kim](https://i-skylabs.slack.com/team/U07KG6S6FMY)1. 이관번호 : 100392. 병원명 : 클리오닉이비인후과의원3. Serial No. / Size : F251121-6DD51 / 9호4. 문의사항 : 데이터 전송시 기기 연결 불가5. 접수시간 : 2026-09-28 09:11:18메타엠_박상윤\u00a0\u00a0[오전 9:58]\\
[**@minseob.song**](https://i-skylabs.slack.com/team/U07K1KTNP8F)1. 이관번호 : 100422. 병원명 : 강남세브란스3. Serial No. / Size : F243801-36361 / 11호4. 문의사항 : 카트링 상태 이상 메시지 / 배터리 불량 교품5. 접수시간 : 2026-09-28 10:00:26 (편집됨)"""

    items = parse_slack_cs_messages(sample)

    assert [item["transfer_no"] for item in items] == ["10039", "10042"]
    assert items[0]["received_at"] == "2026-09-28T09:23:00"
    assert items[1]["received_at"] == "2026-09-28T09:58:00"


def test_hospital_name_keeps_internal_asterisk_until_device_field():
    sample = """메타엠_신유정 [오전 9:08]
@hwihyun.kim
1. 이관번호 : 9991
2. 병원명 : 서울*연합내과
3. Serial No. / Size : F253319-DD1C1 / 12호
4. 문의사항 : 데이터 전송 지연 발생으로 확인 요청
5. 접수시간 : 2026-08-25 09:08:00"""

    items = parse_slack_cs_messages(sample)

    assert len(items) == 1
    assert items[0]["hospital_name"] == "서울*연합내과"


def test_hospital_name_removes_outer_slack_bold_markers_only():
    sample = """메타엠_신유정 [오전 9:08]
@hwihyun.kim
1. 이관번호 : 9992
2. *병원명* : *서울*연합내과*
3. Serial No. / Size : F253319-DD1C1 / 12호
4. 문의사항 : 데이터 전송 지연 발생으로 확인 요청
5. 접수시간 : 2026-08-25 09:08:00"""

    items = parse_slack_cs_messages(sample)

    assert len(items) == 1
    assert items[0]["hospital_name"] == "서울*연합내과"


def test_daewoong_sales_rep_is_removed_from_hospital_name():
    sample = """메타엠_신유정 [오전 9:23]
1. 이관번호 : 10030
2. 병원명 : 대웅제약 이*철 (서울태내과)
3. Serial No. / Size : F253319-DD1C1 / 12호
4. 문의사항 : 데이터 전송 지연 발생으로 확인 요청
5. 접수시간 : 2026-09-28 09:11:18"""

    item = parse_slack_cs_messages(sample)[0]

    assert item["hospital_name"] == "서울태내과"


def test_regular_hospital_parentheses_are_preserved():
    sample = """메타엠_신유정 [오전 9:23]
1. 이관번호 : 10031
2. 병원명 : 건국대학교병원(B)
3. Serial No. / Size : F253319-DD1C1 / 12호
4. 문의사항 : 데이터 전송 지연 발생으로 확인 요청
5. 접수시간 : 2026-09-28 09:11:18"""

    item = parse_slack_cs_messages(sample)[0]

    assert item["hospital_name"] == "건국대학교병원(B)"


def test_daewoong_hospital_with_nested_parentheses_is_preserved():
    sample = """메타엠_신유정 [오전 9:23]
1. 이관번호 : 10032
2. 병원명 : 대웅제약 김*옥 (조대부속병원(B))
3. Serial No. / Size : F253319-DD1C1 / 12호
4. 문의사항 : 데이터 전송 지연 발생으로 확인 요청
5. 접수시간 : 2026-09-28 09:11:18"""

    item = parse_slack_cs_messages(sample)[0]

    assert item["hospital_name"] == "조대부속병원(B)"


def test_cart_bp_accepts_other_sender_and_renumbered_fields():
    sample = """Eunjoo Jeong  [오전 9:25]

이관번호 : 9611
고객명 : 고*태
문의사항 : 본품을 여러 차례 시도해도 측정이 안되어 반품 요청

juhee.lee2  [오후 12:54]
@hwihyun.kim
6. 이관번호 : 9642
7. Serial No. / Size : G261147-7F651
8. 문의사항 : 등록 해제 후 재등록 진행 시 등록 불가"""

    items = parse_slack_cs_messages(sample, "cart_bp")

    assert [item["transfer_no"] for item in items] == ["9611", "9642"]
    assert items[0]["received_at"] == ""
    assert items[1]["serial_no"] == "G261147-7F651"
    assert items[1]["cx_transfer"] == "O"


def test_cart_bp_accepts_free_form_request_without_question_label():
    sample = """Eunjoo Jeong  [오전 9:25]

이관번호 : 9611
고객명 : 고*태 (공유 885 / 연락처 0502-5072-1004)
8/31 본품 9호 배송되어 받았으나 10여차례 시도해도 측정이 안되어 쿠팡으로 반품요청 왔습니다. 고객에게 다시 확인 및 본품 반품 받아야 합니다.
(편집됨)
image.png
메타엠_신유정  [오후 12:05]
@hwihyun.kim
1. 이관번호 : 9619
4. 문의사항 : 데이터 미노출 문의
5. 접수시간 : 2026-09-02 11:58:02"""

    items = parse_slack_cs_messages(sample, "cart_bp")

    assert [item["transfer_no"] for item in items] == ["9611", "9619"]
    assert "반품요청" in items[0]["raw_description"]
    assert "메타엠_신유정" not in items[0]["raw_description"]


def test_cradle_red_led_not_lit_is_not_classified_as_blinking():
    sample = """메타엠_신유정 [오후 4:40]
1. 이관번호 : 9714
2. 병원명 : 대웅제약 손*희 (장이편한내과의원)
3. Serial No. / Size : F251315-E5891 / 10호
4. 문의사항 : 크래들 RED LED 미점등 / 충전 불량 및 기기 이상 여부 확인요청
크래들에 카트링 거치 시 RED LED 미점등, 완충시에만 GREEN LED는 보인다고 함
기기 전원 확인 - 링 센서 O, BT 목록 O
5. 접수시간 : 2026-09-07 16:39:07"""

    item = parse_slack_cs_messages(sample, "cart_bp_pro")[0]

    assert item["category_main"] == "기기 고장"
    assert item["category_sub"] == "크래들 LED 안켜짐"
    assert "미점등" in item["description"]


def test_incidental_recovery_word_does_not_override_charging_failure():
    sample = """1. 이관번호 : 10064
2. 병원명 : 연세대학교 예방의학교실_2
3. Serial No. / Size : G261212-66E21 / 8호
4. 문의사항 : 완충 불가로 기기 이상 여부 확인 요청
기기 연결 불가, 링 센서 X, BT 목록 X
크래들 거치시 RED LED 점등 이후 유지, 23일 카트링 회수 후 오늘까지 지속 충전하였으나 완충 불가
케이블 교차 테스트 시 동일 / 타 크래들 교차 테스트시 RED 점등됨
5. 접수시간 : 2026-09-28 15:37:43 (편집됨)"""

    item = parse_slack_cs_messages(sample, "cart_bp_pro")[0]

    assert item["category_main"] == "기기 고장"
    assert item["category_sub"] == "충전 불량"
    assert item["description"] == "충전 불량 문의"


def test_legacy_complaint_labels_are_accepted():
    sample = """1. 이관 번호 : 2930
2. 병원명 : 성모스마일내과의원
3. Serial No. / Size : F243201-5FBC1 / 10호
4. 불만사항 : 기기 상태 이상 메시지 / 배터리 성능저하 교품
5. 불만 현상 발생 시점 : 2025-07-01 09:09:01
메타엠_정연재 [오전 9:15]
1. 이관 번호 : 2931
2. 병원명 : 연세정성내과의원
3. Serial No. / Size : F250606-89311 / 8호
4. 불만사항 : 기기 상태 이상 메시지 / 배터리 성능저하 교품
5. 불만 현상 발생 시점 : 2025-07-01 09:11:01"""

    items = parse_slack_cs_messages(sample, "cart_bp_pro")

    assert [item["transfer_no"] for item in items] == ["2930", "2931"]
    assert items[0]["hospital_name"] == "성모스마일내과의원"
    assert items[0]["received_at"] == "2025-07-01T09:09:01"
    assert items[1]["received_at"] == "2025-07-01T09:15:00"


def test_pro_inquiry_without_serial_is_still_imported():
    sample = """1. 이관 번호 : 2946
2. 병원명 : 연세우리내과
3. Serial No. / Size :
4. 불만사항 : 이전 교품건 미회수 / 재회수 접수 요청
5. 불만 현상 발생 시점 : 2025-07-01 14:03:01"""

    item = parse_slack_cs_messages(sample, "cart_bp_pro")[0]

    assert item["transfer_no"] == "2946"
    assert item["serial_no"] == ""
    assert item["size"] == ""


def test_multiple_device_text_uses_first_serial_as_representative():
    sample = """1. 이관 번호 : 2952
2. 병원명 : 서울아산류마내과의원
3. Serial No. / Size : 하기 기재
F251112-2F2C1 - 11호
F251102-EC911 - 9호
4. 불만사항 : 정품인증 불가 팝업 / 병원 이동 요청
5. 불만 현상 발생 시점 : 2025-07-02 09:25:02"""

    item = parse_slack_cs_messages(sample, "cart_bp_pro")[0]

    assert item["serial_no"] == "F251112-2F2C1"
    assert item["size"] == "11호"


if __name__ == "__main__":
    test_parse_example_messages()
    test_hospital_name_keeps_internal_asterisk_until_device_field()
    test_hospital_name_removes_outer_slack_bold_markers_only()
    print("slack parser test ok")