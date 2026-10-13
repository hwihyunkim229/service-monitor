from fastapi import APIRouter, Request, Form, Body, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import String, asc, cast, desc, func, or_
from fastapi.responses import StreamingResponse, RedirectResponse
from app.database import SessionLocal
from app.models.cs import CSCase
from app.models.cx import CXAnalysis
from app.models.quality import QualityInspection
from app.models.return_transfer import (
    ReturnTransferHistory,
    ReturnTransferItem,
    ReturnTransferRequest,
)
from app.models.rnd_transfer import RndTransferRequest
from app.utils.query_filters import apply_column_filters
from app.utils.downloads import dated_excel_disposition
from app.services.products import is_bp_product
from io import BytesIO
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
KST = ZoneInfo("Asia/Seoul")


def parse_return_date(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, pattern)
        except ValueError:
            pass
    for separator in ("/", "-"):
        parts = raw.split(separator)
        if len(parts) == 2 and all(part.strip().isdigit() for part in parts):
            try:
                return datetime(datetime.now().year, int(parts[0]), int(parts[1]))
            except ValueError:
                break
    raise ValueError("회수일은 yyyy-mm-dd 또는 M/D 형식으로 입력해 주세요.")


@router.get("/{product}/exchange")
def exchange_list(request: Request, product: str):
    db: Session = SessionLocal()
    keyword = request.query_params.get("keyword", "").strip()

    sort = request.query_params.get("sort", "received_at")
    order = request.query_params.get("order", "desc")
    order = order if order in {"asc", "desc"} else "desc"
    effective_received_at = func.coalesce(CSCase.date_only, CSCase.received_at)
    sort_map = {
        "received_at": effective_received_at,
        "week": CSCase.week,
        "transfer_no": CSCase.transfer_no,
    }

    sort_column = sort_map.get(sort, CSCase.received_at)
    order_func = asc if order == "asc" else desc

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

    query = (
        db.query(CSCase, CXAnalysis)
        .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
        .filter(
            CSCase.product == product,
            func.lower(func.trim(CSCase.exchange_type)).in_(["o", "cradle"]),
        )
    )

    if keyword:
        token = f"%{keyword}%"
        search_columns = [
            CSCase.transfer_no,
            effective_received_at,
            CSCase.return_at,
            CSCase.week,
            CSCase.hospital_name,
            CSCase.category_main,
            CSCase.category_sub,
            CSCase.exchange_type,
            CSCase.serial_no,
            CSCase.replacement_serial_no,
            CSCase.lot,
            CSCase.size,
            CXAnalysis.issue1,
            CXAnalysis.depth1_1,
            CXAnalysis.depth1_2,
            CXAnalysis.issue2,
            CXAnalysis.depth2_1,
            CXAnalysis.depth2_2,
            CXAnalysis.result_summary,
        ]
        query = query.filter(
            or_(*[cast(column, String).ilike(token) for column in search_columns])
        )
    query = apply_column_filters(
        query,
        request.query_params,
        {
            "월": effective_received_at,
            "주": CSCase.week,
            "식별코드": CSCase.transfer_no,
            "접수일": effective_received_at,
            "회수일": CSCase.return_at,
            "병원명": CSCase.hospital_name,
            "유형(대)": CSCase.category_main,
            "유형(소)": CSCase.category_sub,
            "교품": CSCase.exchange_type,
            "고장 S/N": CSCase.serial_no,
            "LOT": CSCase.lot,
            "Size": CSCase.size,
            "교체 S/N": CSCase.replacement_serial_no,
        },
        {"유형(대)", "유형(소)", "교품"},
    )

    total_count = query.count()

    total_pages = (total_count + limit - 1) // limit

    exchange_list = (
        query.order_by(order_func(sort_column)).offset(offset).limit(limit).all()
    )

    db.close()

    return templates.TemplateResponse(
        "exchange_list.html",
        {
            "request": request,
            "exchange_list": exchange_list,
            "sort": sort,
            "order": order,
            "product": product,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "keyword": keyword,
            "bp_profile": is_bp_product(product),
        },
    )


@router.get("/{product}/exchange/excel")
def exchange_excel(request: Request, product: str):
    """Keep the legacy URL aligned with the page's canonical Excel export."""
    from app.routers.workspace import excel_download

    return excel_download(request, product, "exchange")


@router.post("/{product}/exchange/return-date")
def update_return_date(
    product: str, cs_id: int = Form(...), return_at: str = Form(...)
):
    db = SessionLocal()

    cs = db.query(CSCase).filter(CSCase.id == cs_id, CSCase.product == product).first()

    if not cs:
        db.close()
        return RedirectResponse(f"/{product}/exchange", status_code=303)

    if return_at:
        try:
            cs.return_at = parse_return_date(return_at)
            cs.return_at_manual = True
        except ValueError:
            db.close()
            return RedirectResponse(f"/{product}/exchange", status_code=303)
    else:
        cs.return_at = None
        cs.return_at_manual = True

    db.commit()
    db.close()

    return RedirectResponse(f"/{product}/exchange", status_code=303)


@router.post("/{product}/exchange/update/{cs_id}")
def update_exchange_fields(product: str, cs_id: int, data: dict = Body(...)):
    field = data.get("field")
    if field not in {"invoice_no", "replacement_serial_no", "return_at"}:
        raise HTTPException(status_code=400, detail="수정할 수 없는 항목입니다.")
    db = SessionLocal()
    cs = db.query(CSCase).filter(CSCase.id == cs_id, CSCase.product == product).first()
    if not cs:
        db.close()
        raise HTTPException(status_code=404, detail="교품 건을 찾을 수 없습니다.")
    if field == "return_at":
        try:
            parsed = parse_return_date(data.get("value"))
        except ValueError as error:
            db.close()
            raise HTTPException(status_code=400, detail=str(error)) from error
        cs.return_at = parsed
        cs.return_at_manual = True
        received_at = cs.date_only or cs.received_at
        normalized = parsed.strftime("%Y-%m-%d") if parsed else ""
        lead_time = (
            (parsed.date() - received_at.date()).days
            if parsed and received_at
            else None
        )
    else:
        setattr(cs, field, (data.get("value") or "").strip() or None)
        if field == "replacement_serial_no":
            cs.replacement_serial_no_manual = True
        normalized = getattr(cs, field) or ""
        lead_time = None
    db.commit()
    db.close()
    return {
        "status": "ok",
        "value": normalized,
        "lead_time": lead_time,
        "source": (
            "manual"
            if field in {"return_at", "replacement_serial_no"}
            else None
        ),
    }


@router.post("/{product}/exchange/transfer-request")
def request_quality_transfer(product: str, data: dict = Body(...)):
    request_type = (data.get("request_type") or "").strip().lower()
    urgent = bool(data.get("urgent"))
    if request_type not in {"set", "ring", "cradle"}:
        raise HTTPException(status_code=400, detail="이관 유형을 선택하세요.")
    raw_serials = data.get("serial_nos") or []
    if isinstance(raw_serials, str):
        raw_serials = raw_serials.replace(",", "\n").splitlines()
    serials = list(
        dict.fromkeys(
            serial.strip() for serial in raw_serials if serial and serial.strip()
        )
    )
    if not serials:
        raise HTTPException(status_code=400, detail="Serial No를 하나 이상 입력하세요.")

    db = SessionLocal()
    try:
        cases = (
            db.query(CSCase)
            .filter(
                CSCase.product == product,
                CSCase.serial_no.in_(serials),
            )
            .all()
        )
        case_by_serial = {case.serial_no: case for case in cases}
        missing = [serial for serial in serials if serial not in case_by_serial]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"등록되지 않은 시리얼넘버입니다: {', '.join(missing)}",
            )

        unreturned = [
            serial for serial in serials if not case_by_serial[serial].return_at
        ]
        if unreturned:
            raise HTTPException(
                status_code=400,
                detail=f"회수일이 입력되지 않아 요청할 수 없습니다: {', '.join(unreturned)}",
            )

        case_ids = [case_by_serial[serial].id for serial in serials]
        duplicate = (
            db.query(ReturnTransferItem)
            .join(ReturnTransferRequest)
            .filter(
                ReturnTransferItem.cs_id.in_(case_ids),
                ReturnTransferRequest.product == product,
                ReturnTransferRequest.status == "requested",
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=409, detail="이미 승인 대기 중인 이관 요청입니다."
            )

        request_ids = []
        for serial in serials:
            case = case_by_serial[serial]
            transfer_request = ReturnTransferRequest(
                product=product,
                request_type=request_type,
                status="requested",
                urgent=urgent,
            )
            db.add(transfer_request)
            db.flush()
            db.add(
                ReturnTransferItem(
                    request_id=transfer_request.id,
                    cs_id=case.id,
                    transfer_no=case.transfer_no,
                    serial_no=case.serial_no,
                    item_type=request_type,
                )
            )
            db.add(
                ReturnTransferHistory(
                    request_id=transfer_request.id, status="requested"
                )
            )
            request_ids.append(transfer_request.id)
        db.commit()
        return {"status": "requested", "request_ids": request_ids}
    finally:
        db.close()


@router.get("/{product}/exchange/transfer-requests")
def list_transfer_requests(product: str, status: str = None):
    db = SessionLocal()
    try:
        query = db.query(ReturnTransferRequest).filter(
            ReturnTransferRequest.product == product
        )
        if status:
            query = query.filter(ReturnTransferRequest.status == status)
        requests = query.order_by(ReturnTransferRequest.requested_at.desc()).all()
        return {
            "requests": [
                {
                    "id": request.id,
                    "request_type": request.request_type,
                    "urgent": bool(request.urgent),
                    "status": request.status,
                    "rejection_reason": request.rejection_reason,
                    "requested_at": (
                        request.requested_at.isoformat()
                        if request.requested_at
                        else None
                    ),
                    "reviewed_at": (
                        request.reviewed_at.isoformat() if request.reviewed_at else None
                    ),
                    "items": [
                        {"serial_no": item.serial_no, "item_type": item.item_type}
                        for item in request.items
                    ],
                    "history": [
                        {
                            "status": history.status,
                            "reason": history.reason,
                            "created_at": (
                                history.created_at.isoformat()
                                if history.created_at
                                else None
                            ),
                        }
                        for history in sorted(
                            request.history, key=lambda entry: entry.created_at
                        )
                    ],
                }
                for request in requests
            ]
        }
    finally:
        db.close()


@router.get("/{product}/exchange/transfer-history")
def transfer_history_page(request: Request, product: str):
    db = SessionLocal()
    try:
        requests = (
            db.query(ReturnTransferRequest)
            .filter(ReturnTransferRequest.product == product)
            .order_by(ReturnTransferRequest.requested_at.desc())
            .all()
        )
        history_rows = [
            {
                "id": transfer_request.id,
                "flow": "quality",
                "urgent": bool(transfer_request.urgent),
                "request_type": transfer_request.request_type,
                "status": transfer_request.status,
                "rejection_reason": transfer_request.rejection_reason,
                "requested_at": (
                    transfer_request.requested_at.isoformat()
                    if transfer_request.requested_at
                    else ""
                ),
                "reviewed_at": (
                    transfer_request.reviewed_at.isoformat()
                    if transfer_request.reviewed_at
                    else ""
                ),
                "items": [
                    {"serial_no": item.serial_no, "item_type": item.item_type}
                    for item in transfer_request.items
                ],
                "history": [
                    {
                        "status": entry.status,
                        "reason": entry.reason,
                        "created_at": (
                            entry.created_at.isoformat() if entry.created_at else ""
                        ),
                    }
                    for entry in sorted(
                        transfer_request.history, key=lambda entry: entry.created_at
                    )
                ],
            }
            for transfer_request in requests
        ]
        rnd_requests = (
            db.query(RndTransferRequest)
            .filter(RndTransferRequest.product == product)
            .order_by(RndTransferRequest.requested_at.desc())
            .all()
        )
        history_rows.extend(
            {
                "id": transfer_request.id,
                "flow": "rnd",
                "request_type": transfer_request.request_type or "set",
                "urgent": bool(transfer_request.urgent),
                "status": transfer_request.status,
                "rejection_reason": transfer_request.rejection_reason,
                "requested_at": transfer_request.requested_at.isoformat() if transfer_request.requested_at else "",
                "reviewed_at": transfer_request.reviewed_at.isoformat() if transfer_request.reviewed_at else "",
                "items": [
                    {"serial_no": item.serial_no, "item_type": transfer_request.request_type or "set"}
                    for item in transfer_request.items
                ],
                "history": [
                    {
                        "status": entry.status,
                        "reason": entry.reason,
                        "created_at": entry.created_at.isoformat() if entry.created_at else "",
                    }
                    for entry in sorted(transfer_request.history, key=lambda entry: entry.created_at)
                ],
            }
            for transfer_request in rnd_requests
        )
        history_rows.sort(key=lambda row: row["requested_at"], reverse=True)
        return templates.TemplateResponse(
            "transfer_history.html",
            {"request": request, "product": product, "history_rows": history_rows},
        )
    finally:
        db.close()


@router.get("/{product}/exchange/transfer-history/excel")
def transfer_history_excel(product: str):
    status_labels = {
        "requested": "승인 대기",
        "approved": "승인 완료",
        "rejected": "반려",
    }
    type_labels = {"set": "세트", "ring": "Ring", "cradle": "Cradle"}
    db = SessionLocal()
    try:
        requests = (
            db.query(ReturnTransferRequest)
            .filter(ReturnTransferRequest.product == product)
            .order_by(ReturnTransferRequest.requested_at.desc())
            .all()
        )
        rnd_requests = db.query(RndTransferRequest).filter(RndTransferRequest.product == product).all()
        data = []
        combined_requests = [("교품/회수 → 품질", row) for row in requests] + [
            ("품질 → 연구소", row) for row in rnd_requests
        ]
        combined_requests.sort(key=lambda pair: pair[1].requested_at or datetime.min, reverse=True)
        for flow_label, transfer_request in combined_requests:

            def kst_time(value):
                return (
                    value.replace(tzinfo=ZoneInfo("UTC"))
                    .astimezone(KST)
                    .strftime("%Y-%m-%d %H:%M:%S")
                    if value
                    else ""
                )

            history_text = "\n".join(
                f"{status_labels.get(entry.status, entry.status)} · {kst_time(entry.created_at)}"
                + (f" · {entry.reason}" if entry.reason else "")
                for entry in sorted(
                    transfer_request.history, key=lambda entry: entry.created_at
                )
            )
            data.append(
                {
                    "이관 구간": flow_label,
                    "요청일(KST)": kst_time(transfer_request.requested_at),
                    "이관 단위": type_labels.get(
                        transfer_request.request_type, transfer_request.request_type
                    ),
                    "Serial No": "\n".join(
                        item.serial_no for item in transfer_request.items
                    ),
                    "처리 상태": status_labels.get(
                        transfer_request.status, transfer_request.status
                    ),
                    "긴급": "긴급" if transfer_request.urgent else "일반",
                    "처리 이력": history_text,
                    "반려 사유": transfer_request.rejection_reason or "",
                }
            )
    finally:
        db.close()

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(data).to_excel(writer, index=False, sheet_name="회수품 이관 이력")
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=return_transfer_history.xlsx"
        },
    )


@router.post("/{product}/exchange/transfer-requests/{request_id}/review")
def review_transfer_request(product: str, request_id: int, data: dict = Body(...)):
    decision = (data.get("decision") or "").strip().lower()
    reason = (data.get("reason") or "").strip()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="승인 또는 반려를 선택하세요.")
    if decision == "rejected" and not reason:
        raise HTTPException(status_code=400, detail="반려 사유를 입력하세요.")

    db = SessionLocal()
    try:
        transfer_request = (
            db.query(ReturnTransferRequest)
            .filter(
                ReturnTransferRequest.id == request_id,
                ReturnTransferRequest.product == product,
            )
            .first()
        )
        if not transfer_request:
            raise HTTPException(status_code=404, detail="이관 요청을 찾을 수 없습니다.")
        if transfer_request.status != "requested":
            raise HTTPException(status_code=409, detail="이미 처리된 이관 요청입니다.")

        if decision == "approved":
            for item in transfer_request.items:
                case = db.query(CSCase).filter(CSCase.id == item.cs_id).first()
                if not case or not case.return_at:
                    raise HTTPException(
                        status_code=400,
                        detail=f"회수일이 없는 시리얼넘버입니다: {item.serial_no}",
                    )
                quality = (
                    db.query(QualityInspection)
                    .filter(QualityInspection.transfer_no == item.transfer_no)
                    .first()
                )
                if not quality:
                    db.add(QualityInspection(transfer_no=item.transfer_no))

        transfer_request.status = decision
        transfer_request.reviewed_at = datetime.utcnow()
        transfer_request.rejection_reason = reason if decision == "rejected" else None
        db.add(
            ReturnTransferHistory(
                request_id=transfer_request.id,
                status=decision,
                reason=reason or None,
            )
        )
        db.commit()
        return {"status": decision}
    finally:
        db.close()
