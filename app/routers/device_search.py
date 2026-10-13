from collections import defaultdict
from datetime import date
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_

from app.database import SessionLocal
from app.models.cs import CSCase
from app.models.device_warranty import DeviceWarranty
from app.models.exchange import ExchangeInfo
from app.models.lab import LabAnalysis
from app.models.quality import QualityInspection
from app.models.return_transfer import (
    ReturnTransferHistory,
    ReturnTransferItem,
    ReturnTransferRequest,
)
from app.models.rnd_transfer import (
    RndTransferHistory,
    RndTransferItem,
    RndTransferRequest,
)
from app.services.products import list_products
from app.services.device_lineage import SERIAL_PATTERN, build_lineages, normalize_serial

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_LABELS = {
    "requested": "이관 요청",
    "approved": "이관 승인",
    "rejected": "이관 반려",
}
def date_text(value):
    return value.strftime("%Y-%m-%d") if value else ""


def warranty_end(purchase_date):
    if not purchase_date:
        return None
    try:
        return purchase_date.replace(year=purchase_date.year + 1)
    except ValueError:
        return purchase_date.replace(year=purchase_date.year + 1, day=28)


def warranty_devices(db, product, keyword="", status="all", page=1, per_page=50):
    """List every received device, including devices without an exchange edge."""
    cases = (
        db.query(CSCase)
        .filter(CSCase.product == product, CSCase.serial_no.isnot(None))
        .order_by(CSCase.received_at.desc(), CSCase.id.desc())
        .all()
    )
    latest_by_serial = {}
    for case in cases:
        serial = normalize_serial(case.serial_no)
        if not SERIAL_PATTERN.fullmatch(serial) or serial in latest_by_serial:
            continue
        latest_by_serial[serial] = case

    serials = set(latest_by_serial)
    warranty_by_serial = {
        item.root_serial_no: item
        for item in db.query(DeviceWarranty)
        .filter(DeviceWarranty.root_serial_no.in_(serials))
        .all()
    }
    registered = serials & set(warranty_by_serial)
    normalized_keyword = keyword.strip().upper()
    items = []
    for serial in sorted(serials):
        case = latest_by_serial[serial]
        warranty = warranty_by_serial.get(serial)
        if status == "registered" and not warranty:
            continue
        if status == "missing" and warranty:
            continue
        if normalized_keyword and normalized_keyword not in serial and normalized_keyword not in str(case.hospital_name or "").upper():
            continue
        items.append(
            {
                "serial": serial,
                "hospital_name": case.hospital_name or "-",
                "received_date": date_text(case.received_at or case.date_only),
                "purchase_date": warranty.purchase_date if warranty else None,
            }
        )

    page_count = max(1, (len(items) + per_page - 1) // per_page)
    page = min(max(page, 1), page_count)
    start = (page - 1) * per_page
    return {
        "total": len(serials),
        "registered": len(registered),
        "missing": len(serials - registered),
        "devices": items[start : start + per_page],
        "filtered": len(items),
        "page": page,
        "page_count": page_count,
        "has_prev": page > 1,
        "has_next": page < page_count,
        "prev_page": page - 1,
        "next_page": page + 1,
    }


@router.post("/device-search/warranty")
def save_device_warranty(
    root_serial: str = Form(...),
    purchase_date: str = Form(...),
    return_q: str = Form(""),
    return_product: str = Form(""),
    return_warranty_q: str = Form(""),
    return_warranty_status: str = Form("all"),
    return_warranty_page: int = Form(1),
    return_view: str = Form("lineage"),
):
    serial = normalize_serial(root_serial)
    try:
        parsed_date = date.fromisoformat(purchase_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="구매일 형식이 올바르지 않습니다.") from exc
    if not serial:
        raise HTTPException(status_code=400, detail="최초 기기 S/N가 필요합니다.")

    db = SessionLocal()
    try:
        warranty = db.query(DeviceWarranty).filter_by(root_serial_no=serial).first()
        if warranty:
            warranty.purchase_date = parsed_date
        else:
            db.add(DeviceWarranty(root_serial_no=serial, purchase_date=parsed_date))
        db.commit()
    finally:
        db.close()
    valid_products = {item["code"] for item in list_products()}
    if return_product in valid_products:
        params = {}
        if return_view != "warranty":
            params["q"] = return_q
        if return_warranty_q:
            params["warranty_q"] = return_warranty_q
        if return_warranty_status in {"registered", "missing"}:
            params["warranty_status"] = return_warranty_status
        if return_warranty_page > 1:
            params["warranty_page"] = return_warranty_page
        view = "warranty" if return_view == "warranty" else "lineage"
        query = f"?{urlencode(params)}" if params else ""
        return_url = f"/{return_product}/exchange/{view}{query}"
    else:
        return_url = f"/device-search?q={quote(return_q)}"
    return RedirectResponse(return_url, status_code=303)


@router.get("/{product}/exchange/warranty")
def warranty_device_list(request: Request, product: str):
    valid_products = {item["code"] for item in list_products()}
    if product not in valid_products:
        raise HTTPException(status_code=404, detail="제품을 찾을 수 없습니다.")
    keyword = request.query_params.get("warranty_q", "").strip()
    status = request.query_params.get("warranty_status", "all")
    if status not in {"all", "registered", "missing"}:
        status = "all"
    try:
        page = max(int(request.query_params.get("warranty_page", "1")), 1)
    except ValueError:
        page = 1

    db = SessionLocal()
    try:
        summary = warranty_devices(
            db, product, keyword=keyword, status=status, page=page
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        "warranty_devices.html",
        {
            "request": request,
            "product": product,
            "warranty_summary": summary,
            "warranty_q": keyword,
            "warranty_status": status,
        },
    )


@router.get("/device-search")
@router.get("/{product}/exchange/lineage")
def device_search(request: Request, product: str | None = None):
    keyword = request.query_params.get("q", "").strip()
    lineage_mode = product is not None
    lineage_product = product or "cart_bp_pro"
    product_labels = {item["code"]: item["name"] for item in list_products()}
    rows = []
    lineages = []

    db = SessionLocal()
    try:
        if not keyword:
            return templates.TemplateResponse(
                "device_search.html",
                {
                    "request": request,
                    "keyword": keyword,
                    "rows": rows,
                    "lineages": lineages,
                    "lineage_mode": lineage_mode,
                    "product": lineage_product,
                },
            )
        pattern = f"%{keyword}%"
        matched_transfer_nos = [
            value
            for (value,) in db.query(ExchangeInfo.transfer_no)
            .filter(
                or_(
                    ExchangeInfo.serial_no.ilike(pattern),
                    ExchangeInfo.replacement_serial_no.ilike(pattern),
                    ExchangeInfo.transfer_no.ilike(pattern),
                )
            )
            .all()
        ]
        conditions = [
            CSCase.serial_no.ilike(pattern),
            CSCase.replacement_serial_no.ilike(pattern),
            CSCase.transfer_no.ilike(pattern),
            CSCase.hospital_name.ilike(pattern),
        ]
        if matched_transfer_nos:
            conditions.append(CSCase.transfer_no.in_(matched_transfer_nos))
        case_query = db.query(CSCase).filter(or_(*conditions))
        if lineage_mode:
            case_query = case_query.filter(CSCase.product == lineage_product)
        cases = (
            case_query
            .order_by(CSCase.received_at.desc())
            .limit(100)
            .all()
        )
        case_ids = [case.id for case in cases]
        transfer_nos = [case.transfer_no for case in cases if case.transfer_no]

        normalized_keyword = normalize_serial(keyword)
        pro_cases = [case for case in cases if case.product == lineage_product]
        exact_serial_search = any(
            normalized_keyword
            in {
                normalize_serial(case.serial_no),
                normalize_serial(case.replacement_serial_no),
            }
            for case in pro_cases
        )
        seed_serials = (
            {normalized_keyword}
            if exact_serial_search
            else {case.serial_no for case in pro_cases if case.serial_no}
        )

        lineage_cases = (
            db.query(CSCase)
            .filter(
                CSCase.product == lineage_product,
                CSCase.serial_no.isnot(None),
                CSCase.replacement_serial_no.isnot(None),
            )
            .all()
        )
        raw_lineages = build_lineages(lineage_cases, seed_serials)
        replacement_parents = defaultdict(set)
        for case in lineage_cases:
            parent = normalize_serial(case.serial_no)
            child = normalize_serial(case.replacement_serial_no)
            if parent and child:
                replacement_parents[child].add(parent)
        root_serials = {item["root_serial"] for item in raw_lineages}
        warranty_by_serial = {
            item.root_serial_no: item
            for item in db.query(DeviceWarranty)
            .filter(DeviceWarranty.root_serial_no.in_(root_serials))
            .all()
        }
        today = date.today()
        for lineage in raw_lineages:

            if not lineage["steps"]:
                continue
            warranty = warranty_by_serial.get(lineage["root_serial"])
            expires_at = warranty_end(warranty.purchase_date) if warranty else None
            lineage["warranty"] = warranty
            lineage["expires_at"] = expires_at
            lineage["warranty_active"] = bool(expires_at and today < expires_at)
            for step in lineage["steps"]:
                step["shared_child"] = len(
                    replacement_parents.get(step["to_serial"], set())
                ) > 1
                step["parent_serials"] = sorted(
                    replacement_parents.get(step["to_serial"], set())
                )
                step["history"] = [
                    {
                        "date": date_text(case.received_at or case.date_only),
                        "transfer_no": case.transfer_no or "-",
                        "hospital_name": case.hospital_name or "병원 미등록",
                    }
                    for case in step["cases"]
                ]
            lineages.append(lineage)

        exchange_by_transfer = {
            item.transfer_no: item
            for item in db.query(ExchangeInfo)
            .filter(ExchangeInfo.transfer_no.in_(transfer_nos))
            .all()
        }
        quality_by_transfer = {
            item.transfer_no: item
            for item in db.query(QualityInspection)
            .filter(QualityInspection.transfer_no.in_(transfer_nos))
            .all()
        }
        lab_by_transfer = {
            item.transfer_no: item
            for item in db.query(LabAnalysis)
            .filter(LabAnalysis.transfer_no.in_(transfer_nos))
            .all()
        }
        return_by_case = defaultdict(list)
        for item, transfer_request in (
            db.query(ReturnTransferItem, ReturnTransferRequest)
            .join(ReturnTransferRequest)
            .filter(ReturnTransferItem.cs_id.in_(case_ids))
            .all()
        ):
            return_by_case[item.cs_id].append(transfer_request)
        rnd_by_case = defaultdict(list)
        for item, transfer_request in (
            db.query(RndTransferItem, RndTransferRequest)
            .join(RndTransferRequest)
            .filter(RndTransferItem.cs_id.in_(case_ids))
            .all()
        ):
            rnd_by_case[item.cs_id].append(transfer_request)

        return_ids = [
            request.id for requests in return_by_case.values() for request in requests
        ]
        rnd_ids = [
            request.id for requests in rnd_by_case.values() for request in requests
        ]
        return_history = defaultdict(list)
        for entry in (
            db.query(ReturnTransferHistory)
            .filter(ReturnTransferHistory.request_id.in_(return_ids))
            .all()
        ):
            return_history[entry.request_id].append(entry)
        rnd_history = defaultdict(list)
        for entry in (
            db.query(RndTransferHistory)
            .filter(RndTransferHistory.request_id.in_(rnd_ids))
            .all()
        ):
            rnd_history[entry.request_id].append(entry)

        for case in cases:
            exchange = exchange_by_transfer.get(case.transfer_no)
            quality = quality_by_transfer.get(case.transfer_no)
            lab = lab_by_transfer.get(case.transfer_no)
            return_requests = return_by_case.get(case.id, [])
            rnd_requests = rnd_by_case.get(case.id, [])

            if lab and lab.analysis_completed_date:
                current_location = "분석 완료"
            elif lab or any(item.status == "approved" for item in rnd_requests):
                current_location = "연구소"
            elif quality or any(item.status == "approved" for item in return_requests):
                current_location = "품질팀"
            elif any(item.status == "requested" for item in return_requests):
                current_location = "품질 이관 대기"
            elif case.return_at:
                current_location = "서비스 운영팀"
            else:
                current_location = case.hospital_name or "고객/병원"

            events = []

            def add_event(value, title, detail=""):
                if value:
                    events.append(
                        {
                            "sort_value": date_text(value),
                            "date": date_text(value),
                            "title": title,
                            "detail": detail,
                        }
                    )

            add_event(
                case.received_at or case.date_only, "CS 접수", case.transfer_no or ""
            )
            add_event(case.return_at, "회수 완료")
            for transfer_request in return_requests:
                for entry in return_history[transfer_request.id]:
                    add_event(
                        entry.created_at,
                        f"품질팀 {STATUS_LABELS.get(entry.status, entry.status)}",
                        entry.reason or "",
                    )
            if quality and quality.lab_transfer_date:
                add_event(
                    quality.lab_transfer_date,
                    "품질 검사 완료",
                    quality.quality_result or "",
                )
            for transfer_request in rnd_requests:
                for entry in rnd_history[transfer_request.id]:
                    add_event(
                        entry.created_at,
                        f"연구소 {STATUS_LABELS.get(entry.status, entry.status)}",
                        entry.reason or "",
                    )
            if lab and lab.analysis_completed_date:
                add_event(
                    lab.analysis_completed_date,
                    "연구소 분석 완료",
                    lab.analysis_result or lab.root_cause or "",
                )
            events.sort(key=lambda item: item["sort_value"], reverse=True)
            rows.append(
                {
                    "case": case,
                    "product_label": product_labels.get(case.product, case.product),
                    "exchange": exchange,
                    "current_location": current_location,
                    "events": events,
                }
            )
    finally:
        db.close()

    return templates.TemplateResponse(
        "device_search.html",
        {
            "request": request,
            "keyword": keyword,
            "rows": rows,
            "lineages": lineages,
            "lineage_mode": lineage_mode,
            "product": lineage_product,
        },
    )