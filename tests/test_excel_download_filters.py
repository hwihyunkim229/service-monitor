from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from starlette.datastructures import QueryParams
from app.database import Base
from app.models.cs import CSCase
from app.routers.workspace import apply_excel_filters, excel_data


def test_column_filters_keep_only_matching_rows_and_reindex_comments():
    headers = ["식별코드", "주", "병원명"]
    data = [
        ["9001", 33, "가병원"],
        ["9002", 34, "나병원"],
        ["9003", 34, "다병원"],
    ]
    comments = {(3, 3): "두 번째 데이터 행 메모", (4, 3): "제외될 메모"}
    params = QueryParams("cf_주=34&cf_병원명=나")

    filtered, filtered_comments = apply_excel_filters(
        headers, data, comments, params, "cx"
    )

    assert filtered == [["9002", 34, "나병원"]]
    assert filtered_comments == {(2, 3): "두 번째 데이터 행 메모"}


def test_blank_column_filter_token_keeps_empty_excel_cells():
    headers = ["식별코드", "교체 S/N"]
    data = [["9001", ""], ["9002", "G260101-AAAA1"]]

    filtered, _ = apply_excel_filters(
        headers,
        data,
        {},
        QueryParams("cf_교체+S%2FN=__blank__"),
        "exchange",
    )

    assert filtered == [["9001", ""]]


def test_quality_cx_summary_filter_searches_all_related_excel_columns():
    headers = [
        "식별코드",
        "유형(대)",
        "유형(소)",
        "문의사항",
        "CX 원인1",
        "CX Depth1-1",
        "CX Depth1-2",
        "CX 원인2",
        "CX Depth2-1",
        "CX Depth2-2",
    ]
    data = [
        ["9101", "기기 고장", "충전 불량", "확인 요청", "H/W", "충전", "", "", "", ""],
        [
            "9102",
            "리포트 이슈",
            "데이터 누락",
            "다운로드 확인",
            "Server",
            "전송",
            "",
            "",
            "",
            "",
        ],
    ]

    filtered, _ = apply_excel_filters(
        headers, data, {}, QueryParams("cf_CX+정보=데이터+누락"), "quality"
    )

    assert filtered == [data[1]]


def test_quality_page_query_filter_is_applied_to_download():
    headers = ["식별코드", "품질 판정"]
    data = [["9201", "정상"], ["9202", "불량"]]

    filtered, _ = apply_excel_filters(
        headers, data, {}, QueryParams("quality_result=불량"), "quality"
    )

    assert filtered == [["9202", "불량"]]


def test_exchange_download_matches_visible_columns_and_filters():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all(
        [
            CSCase(
                product="cart_bp_pro",
                transfer_no="A-100",
                exchange_type="O",
                date_only=datetime(2025, 4, 7),
                received_at=datetime(2026, 4, 7, 9, 20),
                serial_no="F250101-AAAA1",
                replacement_serial_no="F250201-BBBB1",
                lot="F250101",
                size="9",
            ),
            CSCase(
                product="cart_bp_pro",
                transfer_no="B-200",
                exchange_type="O",
                date_only=datetime(2025, 4, 8),
                serial_no="F250102-CCCC1",
            ),
        ]
    )
    db.commit()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/cart_bp_pro/exchange/excel-download",
            "query_string": b"keyword=A-100",
            "headers": [],
        }
    )

    headers, rows, _comments = excel_data(
        db, request, "cart_bp_pro", "exchange"
    )

    assert headers == [
        "월", "주", "식별코드", "접수일", "회수일", "병원명", "유형(대)",
        "유형(소)", "교품", "고장 S/N", "LOT", "Size", "리드타임", "교체 S/N",
    ]
    assert len(rows) == 1
    assert len(rows[0]) == len(headers)
    assert rows[0][2] == "A-100"
    assert rows[0][3] == "2025-04-07"
    assert rows[0][-1] == "F250201-BBBB1"
    db.close()


if __name__ == "__main__":
    test_column_filters_keep_only_matching_rows_and_reindex_comments()
    test_quality_cx_summary_filter_searches_all_related_excel_columns()
    test_quality_page_query_filter_is_applied_to_download()
    print("excel download filter tests ok")