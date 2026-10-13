from fastapi import APIRouter, Request, Form, Body, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import asc, case, desc, func
from io import BytesIO
import re
from datetime import datetime
from app.database import SessionLocal
from app.models.cs import CSCase
from app.models.quality import QualityInspection
from app.models.cx import CXAnalysis
from app.models.quality_memo import QualityCellMemo
from app.services.quality_schema import QUALITY_COLUMNS, QUALITY_LABELS
from app.services.cx_categories import rev_from_serial
from app.services.process_rev import get_process_rev, import_process_rev_workbook
from app.utils.query_filters import apply_column_filters
from app.models.return_transfer import ReturnTransferItem, ReturnTransferRequest
from app.models.rnd_transfer import (
    RndTransferHistory,
    RndTransferItem,
    RndTransferRequest,
)
from fastapi import UploadFile, File
from app.utils.tabular_upload import read_dict_rows
from sqlalchemy import or_
from sqlalchemy import cast, String

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post("/{product}/quality/rnd-transfer-request")
def request_rnd_transfer(product: str, data: dict = Body(...)):
    request_type = (data.get("request_type") or "").strip().lower()
    urgent = bool(data.get("urgent"))
    if request_type not in {"set", "ring", "cradle"}:
        raise HTTPException(status_code=400, detail="이관 유형을 선택하세요.")
    raw_serials = data.get("serial_nos") or []
    if isinstance(raw_serials, str):
        raw_serials = raw_serials.replace(",", "\n").splitlines()
    serials = list(
        dict.fromkeys(str(value).strip() for value in raw_serials if str(value).strip())
    )
    if not serials:
        raise HTTPException(status_code=400, detail="Serial No를 하나 이상 입력하세요.")

    db = SessionLocal()
    try:
        cases = (
            db.query(CSCase)
            .filter(CSCase.product == product, CSCase.serial_no.in_(serials))
            .all()
        )
        case_by_serial = {case.serial_no: case for case in cases}
        missing = [serial for serial in serials if serial not in case_by_serial]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"등록되지 않은 Serial No입니다: {', '.join(missing)}",
            )

        quality_transfers = {
            value[0]
            for value in db.query(QualityInspection.transfer_no)
            .filter(
                QualityInspection.transfer_no.in_([case.transfer_no for case in cases])
            )
            .all()
        }
        not_in_quality = [
            serial
            for serial in serials
            if case_by_serial[serial].transfer_no not in quality_transfers
        ]
        if not_in_quality:
            raise HTTPException(
                status_code=400,
                detail=f"품질 목록에 없는 Serial No입니다: {', '.join(not_in_quality)}",
            )

        quality_by_transfer = {
            row.transfer_no: row
            for row in db.query(QualityInspection)
            .filter(QualityInspection.transfer_no.in_([case.transfer_no for case in cases]))
            .all()
        }
        incomplete = []
        for serial in serials:
            quality = quality_by_transfer.get(case_by_serial[serial].transfer_no)
            if not quality or quality.quality_result not in {"OK", "NG"} or not quality.lab_transfer_date:
                incomplete.append(serial)
        if incomplete:
            raise HTTPException(
                status_code=400,
                detail=(
                    "품질 판정(OK/NG)과 품질 분석 완료일이 모두 입력되어야 "
                    f"연구소로 이관할 수 있습니다: {', '.join(incomplete)}"
                ),
            )

        duplicate = (
            db.query(RndTransferItem)
            .join(RndTransferRequest)
            .filter(
                RndTransferItem.cs_id.in_(
                    [case_by_serial[serial].id for serial in serials]
                ),
                RndTransferRequest.product == product,
                RndTransferRequest.status.in_(["requested", "approved"]),
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="이미 승인 대기 중이거나 승인 완료된 전달 건이 포함되어 있습니다.",
            )

        transfer_request = RndTransferRequest(
            product=product,
            request_type=request_type,
            urgent=urgent,
            status="requested",
        )
        db.add(transfer_request)
        db.flush()
        for serial in serials:
            case = case_by_serial[serial]
            db.add(
                RndTransferItem(
                    request_id=transfer_request.id,
                    cs_id=case.id,
                    transfer_no=case.transfer_no,
                    serial_no=case.serial_no,
                )
            )
        db.add(RndTransferHistory(request_id=transfer_request.id, status="requested"))
        db.commit()
        return {"status": "requested", "request_id": transfer_request.id}
    finally:
        db.close()


def apply_filter(query, column, value):
    if value == "(blank)":
        return query.filter(or_(column.is_(None), column == ""))
    elif value:
        return query.filter(column.contains(value))
    return query


def get_rev(lot):
    return get_process_rev(lot)


@router.post("/{product}/quality/process-rev-upload")
async def upload_process_rev_master(product: str, file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Excel(.xlsx) 파일을 선택하세요.")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="빈 파일은 업로드할 수 없습니다.")
    try:
        result = import_process_rev_workbook(BytesIO(contents))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="공정 Rev 기준표를 읽지 못했습니다. 원본 형식을 확인하세요.",
        ) from exc
    return result


@router.get("/{product}/quality")
def quality_list(request: Request, product: str):
    keyword = (request.query_params.get("keyword") or "").strip()
    order = request.query_params.get("order", "desc")
    order = order if order in {"asc", "desc"} else "desc"

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        requested_limit = int(request.query_params.get("limit", 100))
    except (TypeError, ValueError):
        requested_limit = 100
    limit = requested_limit if requested_limit in {20, 50, 100} else 100

    offset = (page - 1) * limit

    db = SessionLocal()

    def clean(v):
        return v.strip() if v else ""

    filters = {
        "transfer_no": clean(request.query_params.get("transfer_no")),
        "serial_no": clean(request.query_params.get("serial_no")),
        "lot": clean(request.query_params.get("lot")),
        "rev": clean(request.query_params.get("rev"))
        or clean(request.query_params.get("cf_공정 Rev")),
        "current_self_cradle": clean(request.query_params.get("current_self_cradle")),
        "current_master_ring": clean(request.query_params.get("current_master_ring")),
        "current_master_cradle": clean(
            request.query_params.get("current_master_cradle")
        ),
        "station_charge": clean(request.query_params.get("station_charge")),
        "charge_log": clean(request.query_params.get("charge_log")),
        "battery_3h": clean(request.query_params.get("battery_3h")),
        "disconnect_connectable": clean(
            request.query_params.get("disconnect_connectable")
        ),
        "battery_after_disconnect": clean(
            request.query_params.get("battery_after_disconnect")
        ),
        "signal": clean(request.query_params.get("signal")),
        "bt": clean(request.query_params.get("bt")),
        "appearance": clean(request.query_params.get("appearance")),
        "discharge_time": clean(request.query_params.get("discharge_time")),
        "discharge_result": clean(request.query_params.get("discharge_result")),
        "water_damage": clean(request.query_params.get("water_damage")),
        "cap_height": clean(request.query_params.get("cap_height")),
        "defect_detail": clean(request.query_params.get("defect_detail")),
        "quality_result": clean(request.query_params.get("quality_result")),
        "lab_transfer_date": clean(request.query_params.get("lab_transfer_date")),
    }

    query = (
        db.query(CSCase, QualityInspection, CXAnalysis)
        .outerjoin(ReturnTransferItem, CSCase.id == ReturnTransferItem.cs_id)
        .outerjoin(
            ReturnTransferRequest,
            ReturnTransferItem.request_id == ReturnTransferRequest.id,
        )
        .filter(
            func.lower(func.trim(CSCase.exchange_type)).in_(["o", "cradle"]),
            CSCase.return_at.isnot(None),
            CSCase.product == product,
            or_(
                ReturnTransferRequest.status == "approved",
                QualityInspection.id.isnot(None),  # 기존 품질 데이터는 계속 조회
            ),
            or_(CSCase.serial_no.is_(None), ~CSCase.serial_no.like("F23%")),
            or_(CSCase.serial_no.is_(None), ~CSCase.serial_no.like("F24%")),
        )
        .outerjoin(
            QualityInspection, CSCase.transfer_no == QualityInspection.transfer_no
        )
        .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
    )

    if keyword:
        token = f"%{keyword}%"
        search_columns = [
            CSCase.transfer_no,
            CSCase.serial_no,
            CSCase.lot,
            CSCase.hospital_name,
            CSCase.category_main,
            CSCase.category_sub,
            CSCase.description,
            CSCase.exchange_type,
            CXAnalysis.issue1,
            CXAnalysis.depth1_1,
            CXAnalysis.depth1_2,
            CXAnalysis.issue2,
            CXAnalysis.depth2_1,
            CXAnalysis.depth2_2,
            CXAnalysis.result_summary,
            *QualityInspection.__table__.columns,
        ]
        query = query.filter(
            or_(*[cast(column, String).ilike(token) for column in search_columns])
        )

    if filters["transfer_no"]:
        query = query.filter(CSCase.transfer_no.contains(filters["transfer_no"]))

    if filters["serial_no"]:
        query = query.filter(CSCase.serial_no.contains(filters["serial_no"]))

    if filters["lot"]:
        query = query.filter(CSCase.lot.contains(filters["lot"]))

    query = apply_filter(
        query, QualityInspection.current_self_cradle, filters["current_self_cradle"]
    )
    query = apply_filter(
        query, QualityInspection.current_master_ring, filters["current_master_ring"]
    )
    query = apply_filter(
        query, QualityInspection.current_master_cradle, filters["current_master_cradle"]
    )
    query = apply_filter(
        query, QualityInspection.station_charge, filters["station_charge"]
    )
    query = apply_filter(query, QualityInspection.charge_log, filters["charge_log"])
    query = apply_filter(query, QualityInspection.battery_3h, filters["battery_3h"])
    query = apply_filter(
        query,
        QualityInspection.disconnect_connectable,
        filters["disconnect_connectable"],
    )
    query = apply_filter(
        query,
        QualityInspection.battery_after_disconnect,
        filters["battery_after_disconnect"],
    )
    query = apply_filter(query, QualityInspection.signal, filters["signal"])
    query = apply_filter(query, QualityInspection.bt, filters["bt"])
    query = apply_filter(query, QualityInspection.appearance, filters["appearance"])
    query = apply_filter(
        query, QualityInspection.discharge_time, filters["discharge_time"]
    )
    query = apply_filter(
        query, QualityInspection.discharge_result, filters["discharge_result"]
    )
    query = apply_filter(query, QualityInspection.water_damage, filters["water_damage"])
    query = apply_filter(query, QualityInspection.cap_height, filters["cap_height"])
    query = apply_filter(
        query, QualityInspection.defect_detail, filters["defect_detail"]
    )
    query = apply_filter(
        query, QualityInspection.quality_result, filters["quality_result"]
    )

    if filters["lab_transfer_date"]:
        query = query.filter(
            cast(QualityInspection.lab_transfer_date, String).contains(
                filters["lab_transfer_date"]
            )
        )

    quality_filter_map = {
        "접수일": CSCase.received_at,
        "회수일": CSCase.return_at,
        "회수품 이관일": QualityInspection.return_transfer_date,
        "병원명": CSCase.hospital_name,
        "식별코드": CSCase.transfer_no,
        "S/N": CSCase.serial_no,
        "HW Rev": CSCase.rev,
        "CX 정보": [
            CSCase.category_main,
            CSCase.category_sub,
            CSCase.description,
            CXAnalysis.issue1,
            CXAnalysis.depth1_1,
            CXAnalysis.depth1_2,
            CXAnalysis.issue2,
            CXAnalysis.depth2_1,
            CXAnalysis.depth2_2,
        ],
        **{
            label: getattr(QualityInspection, field) for field, label in QUALITY_COLUMNS
        },
        "품질 분석 완료일": QualityInspection.lab_transfer_date,
    }
    query = apply_column_filters(query, request.query_params, quality_filter_map)

    def with_resolved_rev(result_rows):
        resolved_rows = []
        for cs, q, cx in result_rows:
            serial = cs.serial_no or ""
            lot = serial.split("-")[0] if "-" in serial else serial

            if lot.startswith(("F23", "F24")):
                continue

            rev = q.rev if q and q.rev else get_rev(lot)
            if filters["rev"] and filters["rev"] not in (rev or ""):
                continue
            resolved_rows.append(
                {
                    "cs": cs,
                    "q": q,
                    "cx": cx,
                    "lot": lot,
                    "rev": rev,
                    "hw_rev": cs.rev or rev_from_serial(serial) or "",
                }
            )
        return resolved_rows

    order_fn = asc if order == "asc" else desc
    completed_quality = case(
        (
            QualityInspection.quality_result.in_(["OK", "NG"])
            & QualityInspection.lab_transfer_date.isnot(None),
            1,
        ),
        else_=0,
    )
    ordered_query = query.order_by(
        completed_quality.asc(),
        CSCase.return_at.is_(None),
        order_fn(CSCase.return_at),
        order_fn(CSCase.id),
    )

    if filters["rev"]:
        # Rev may be derived from the LOT rather than stored in the DB, so it
        # must be resolved before pagination to avoid missing later matches.
        matching_rows = with_resolved_rev(ordered_query.all())
        total_count = len(matching_rows)
        total_pages = (total_count + limit - 1) // limit
        rows = matching_rows[offset : offset + limit]
    else:
        total_count = query.count()
        total_pages = (total_count + limit - 1) // limit
        rows = with_resolved_rev(ordered_query.offset(offset).limit(limit).all())

    quality_ids = [r["q"].id for r in rows if r["q"]]

    memos = (
        db.query(QualityCellMemo)
        .filter(QualityCellMemo.quality_id.in_(quality_ids))
        .all()
    )

    memo_map = {(m.quality_id, m.field_name): m.memo for m in memos}
    urgent_transfer_nos = {
        value[0]
        for value in db.query(ReturnTransferItem.transfer_no)
        .join(ReturnTransferRequest)
        .filter(
            ReturnTransferRequest.product == product,
            ReturnTransferRequest.urgent.is_(True),
        )
        .all()
    }

    FILTER_FIELDS = {
        "current_self_cradle": QualityInspection.current_self_cradle,
        "current_master_ring": QualityInspection.current_master_ring,
        "current_master_cradle": QualityInspection.current_master_cradle,
        "station_charge": QualityInspection.station_charge,
        "charge_log": QualityInspection.charge_log,
        "battery_3h": QualityInspection.battery_3h,
        "disconnect_connectable": QualityInspection.disconnect_connectable,
        "battery_after_disconnect": QualityInspection.battery_after_disconnect,
        "signal": QualityInspection.signal,
        "bt": QualityInspection.bt,
        "appearance": QualityInspection.appearance,
        "discharge_time": QualityInspection.discharge_time,
        "discharge_result": QualityInspection.discharge_result,
        "water_damage": QualityInspection.water_damage,
        "cap_height": QualityInspection.cap_height,
        "defect_detail": QualityInspection.defect_detail,
        "quality_result": QualityInspection.quality_result,
    }
    FIELD_LABELS = QUALITY_LABELS

    distinct_values = {}

    for key, column in FILTER_FIELDS.items():
        values = db.query(column).distinct().all()
        values = [v[0] for v in values if v[0] is not None]
        values = sorted(set([str(v) for v in values]))
        distinct_values[key] = values

    response = templates.TemplateResponse(
        "quality_list.html",
        {
            "request": request,
            "rows": rows,
            "memo_map": memo_map,
            "product": product,
            "filters": filters,
            "keyword": keyword,
            "distinct_values": distinct_values,
            "filter_fields": list(FILTER_FIELDS.keys()),
            "field_labels": FIELD_LABELS,
            "quality_columns": QUALITY_COLUMNS,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "urgent_transfer_nos": urgent_transfer_nos,
            "order": order,
        },
    )

    db.close()

    return response


@router.post("/{product}/quality/upload_csv")
async def upload_quality_csv(product: str, file: UploadFile = File(...)):
    contents = await file.read()

    COLUMN_MAP = {
        "식별코드": "transfer_no",
        "Serial No": "serial_no",
        "충전전류(Cradle)": "current_self_cradle",
        "충전전류(Master_Ring)": "current_master_ring",
        "충전전류(Master_Cradle)": "current_master_cradle",
        "충전 가능 여부": "station_charge",
        "충전 후 상태(log)": "charge_log",
        "충전 중 배터리(3h)": "battery_3h",
        "충전 해제 후 연결": "disconnect_connectable",
        "충전 해제 후 배터리(20m)": "battery_after_disconnect",
        "신호": "signal",
        "BT": "bt",
        "외관": "appearance",
        "방전": "discharge_time",
        "방전 후 상태": "discharge_result",
        "침수": "water_damage",
        "캡높이": "cap_height",
        "불량내용": "defect_detail",
        "품질판정": "quality_result",
        "품질 분석 완료일": "lab_transfer_date",
    }

    reader = read_dict_rows(contents, file.filename)

    converted_rows = []

    for row in reader:
        new_row = {}
        for k, v in row.items():
            mapped_key = COLUMN_MAP.get(k)
            if mapped_key:
                new_row[mapped_key] = v
        converted_rows.append(new_row)

    db = SessionLocal()

    def clean(v):
        if v is None:
            return None

        v = str(v).strip()

        if v == "":
            return None

        return v

    updated = 0
    skipped = 0

    for row in converted_rows:

        transfer_no = clean(row.get("transfer_no"))
        serial_no = clean(row.get("serial_no"))

        if not transfer_no or not serial_no:
            skipped += 1
            continue

        cs = (
            db.query(CSCase)
            .filter(
                CSCase.transfer_no == transfer_no,
                CSCase.serial_no == serial_no,
                CSCase.product == product,
            )
            .first()
        )

        if not cs:
            skipped += 1
            continue

        q = (
            db.query(QualityInspection)
            .filter(QualityInspection.transfer_no == transfer_no)
            .first()
        )

        if not q:
            q = QualityInspection(transfer_no=transfer_no)
            db.add(q)
            db.flush()

        for field in [
            "current_self_cradle",
            "current_master_ring",
            "current_master_cradle",
            "station_charge",
            "charge_log",
            "battery_3h",
            "disconnect_connectable",
            "battery_after_disconnect",
            "signal",
            "bt",
            "appearance",
            "discharge_time",
            "discharge_result",
            "water_damage",
            "cap_height",
            "defect_detail",
            "quality_result",
        ]:
            value = row.get(field)

            if value is None:
                continue

            value = str(value).strip()

            if value == "":
                continue

            try:
                value = int(value)
            except:
                pass

            setattr(q, field, value)

        lab_date = clean(row.get("lab_transfer_date"))
        if lab_date:
            try:
                q.lab_transfer_date = datetime.strptime(lab_date, "%Y-%m-%d")
            except:
                pass

        updated += 1

    db.commit()
    db.close()

    print(f"✅ 업데이트: {updated}, ❌ 스킵: {skipped}")

    return RedirectResponse(url=f"/{product}/quality", status_code=303)


@router.post("/{product}/quality/memo")
def save_cell_memo(product: str, data: dict = Body(...)):
    db = SessionLocal()

    quality_id = data.get("quality_id")
    field_name = data.get("field_name")
    memo_text = (data.get("memo") or "").strip()

    if field_name not in {
        "current_self_cradle",
        "current_master_ring",
        "current_master_cradle",
        "station_charge",
        "charge_log",
        "battery_3h",
        "disconnect_connectable",
        "battery_after_disconnect",
        "signal",
        "bt",
        "appearance",
        "discharge_time",
        "discharge_result",
        "water_damage",
        "cap_height",
        "defect_detail",
        "quality_result",
    }:
        db.close()
        raise HTTPException(status_code=400, detail="메모를 저장할 수 없는 항목입니다.")

    quality = (
        db.query(QualityInspection)
        .join(CSCase, CSCase.transfer_no == QualityInspection.transfer_no)
        .filter(QualityInspection.id == quality_id, CSCase.product == product)
        .first()
    )
    if not quality:
        db.close()
        raise HTTPException(status_code=404, detail="품질 분석 건을 찾을 수 없습니다.")

    memo = (
        db.query(QualityCellMemo)
        .filter(
            QualityCellMemo.quality_id == quality_id,
            QualityCellMemo.field_name == field_name,
        )
        .first()
    )

    if not memo:
        memo = QualityCellMemo(quality_id=quality_id, field_name=field_name)
        db.add(memo)

    memo.memo = memo_text

    db.commit()
    db.close()

    return {"status": "success"}


@router.post("/{product}/quality/transfer-date")
def update_lab_transfer_date(
    product: str, transfer_no: str = Form(...), lab_transfer_date: str = Form(...)
):
    db = SessionLocal()

    quality = (
        db.query(QualityInspection)
        .filter(QualityInspection.transfer_no == transfer_no)
        .first()
    )

    if not quality:
        quality = QualityInspection(transfer_no=transfer_no)
        db.add(quality)

    if lab_transfer_date:
        quality.lab_transfer_date = datetime.strptime(
            lab_transfer_date, "%Y-%m-%d"
        ).date()

    db.commit()
    db.close()

    return RedirectResponse(url="/{product}/quality", status_code=303)


@router.get("/{product}/quality/excel")
def quality_excel(request: Request, product: str):
    from app.routers.workspace import excel_download

    return excel_download(request, product, "quality")


@router.get("/{product}/quality/excel-download")
def quality_page_excel_download(request: Request, product: str):
    from app.routers.workspace import excel_download

    return excel_download(request, product, "quality")


@router.get("/{product}/quality/{transfer_no}")
def quality_detail(request: Request, product: str, transfer_no: str):
    db = SessionLocal()

    quality = (
        db.query(QualityInspection)
        .filter(QualityInspection.transfer_no == transfer_no)
        .first()
    )

    cx = db.query(CXAnalysis).filter(CXAnalysis.transfer_no == transfer_no).first()

    cs = (
        db.query(CSCase)
        .filter(CSCase.transfer_no == transfer_no, CSCase.product == product)
        .first()
    )

    db.close()

    return templates.TemplateResponse(
        "quality.html",
        {
            "request": request,
            "quality": quality,
            "cx": cx,
            "cs": cs,
            "product": product,
        },
    )


@router.post("/{product}/quality/save/{transfer_no}")
def save_quality(
    product: str,
    transfer_no: str,
    current_self_cradle: str = Form(None),
    current_master_ring: str = Form(None),
    current_master_cradle: str = Form(None),
    station_charge: str = Form(None),
    charge_log: str = Form(None),
    battery_3h: str = Form(None),
    disconnect_connectable: str = Form(None),
    battery_after_disconnect: str = Form(None),
    signal: str = Form(None),
    bt: str = Form(None),
    appearance: str = Form(None),
    discharge_time: str = Form(None),
    discharge_result: str = Form(None),
    water_damage: str = Form(None),
    cap_height: str = Form(None),
    defect_detail: str = Form(None),
    quality_result: str = Form(None),
):
    db = SessionLocal()

    quality = (
        db.query(QualityInspection)
        .filter(QualityInspection.transfer_no == transfer_no)
        .first()
    )

    if not quality:
        quality = QualityInspection(transfer_no=transfer_no)
        db.add(quality)

    quality.current_self_cradle = current_self_cradle
    quality.current_master_ring = current_master_ring
    quality.current_master_cradle = current_master_cradle
    quality.station_charge = station_charge
    quality.charge_log = charge_log
    quality.battery_3h = battery_3h
    quality.disconnect_connectable = disconnect_connectable
    quality.battery_after_disconnect = battery_after_disconnect
    quality.signal = signal
    quality.bt = bt
    quality.appearance = appearance
    quality.discharge_time = discharge_time
    quality.discharge_result = discharge_result
    quality.water_damage = water_damage
    quality.cap_height = cap_height
    quality.defect_detail = defect_detail
    quality.quality_result = quality_result

    db.commit()
    db.close()

    return RedirectResponse(url=f"/quality/{transfer_no}", status_code=302)


@router.post("/{product}/quality/update/{item_id}")
def update_quality_cell(
    product: str,
    item_id: int,
    data: dict = Body(...),
):
    db = SessionLocal()

    quality = (
        db.query(QualityInspection).filter(QualityInspection.id == item_id).first()
    )

    if not quality:
        db.close()
        return {"error": "not found"}

    field = data.get("field")
    value = data.get("value")

    if not hasattr(quality, field):
        db.close()
        return {"error": "invalid field"}

    if field == "quality_result":
        value = str(value or "대기").strip().upper()
        if value not in {"대기", "OK", "NG"}:
            db.close()
            raise HTTPException(400, "품질 판정은 대기, OK, NG 중에서 선택해 주세요.")

    if field in {"lab_transfer_date", "return_transfer_date"} and value:
        raw_value = str(value).strip()
        parsed = None
        for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(raw_value, pattern).date()
                break
            except ValueError:
                pass
        if parsed is None:
            match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", raw_value)
            if match:
                try:
                    parsed = datetime(
                        datetime.now().year, int(match.group(1)), int(match.group(2))
                    ).date()
                except ValueError:
                    pass
        if parsed is None:
            db.close()
            raise HTTPException(
                400, "일자는 yyyy-mm-dd 또는 M/D 형식으로 입력해 주세요."
            )
        value = parsed
    elif field in {"lab_transfer_date", "return_transfer_date"}:
        value = None

    setattr(quality, field, value)

    db.commit()
    completed = (
        quality.quality_result in {"OK", "NG"} and quality.lab_transfer_date is not None
    )
    db.close()

    return {
        "status": "success",
        "value": value.isoformat() if hasattr(value, "isoformat") else value,
        "completed": completed,
    }
