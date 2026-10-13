from io import BytesIO
from openpyxl import Workbook
from app.services.process_rev import read_process_rev_workbook


def test_simple_process_rev_upload_format_does_not_require_ring_rev():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "업로드 양식"
    sheet.append(["LOT No.", "출고 Rev"])
    sheet.append(["G261801", 5])
    sheet.append(["N263401", "Rev6"])
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    assert read_process_rev_workbook(stream) == {
        "G261801": {"process_rev": "Rev5"},
        "N263401": {"process_rev": "Rev6"},
    }


def test_simple_process_rev_upload_rejects_conflicting_duplicate_lot():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["LOT No.", "출고 Rev"])
    sheet.append(["G261801", 5])
    sheet.append(["G261801", 6])
    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    try:
        read_process_rev_workbook(stream)
    except ValueError as exc:
        assert "중복된 LOT" in str(exc)
    else:
        raise AssertionError("서로 다른 Rev에 중복된 LOT를 허용했습니다.")