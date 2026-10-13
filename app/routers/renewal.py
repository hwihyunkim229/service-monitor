from datetime import datetime
import json
from pathlib import Path
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Integer, String, and_, asc, case, cast, desc, func, or_
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.dependencies.auth import login_required
from app.models.cs import CSCase
from app.models.cx import CXAnalysis
from app.models.exchange import ExchangeInfo
from app.models.lab import LabAnalysis
from app.models.loss_reason import LossReason
from app.models.quality import QualityInspection
from app.models.quality_memo import QualityCellMemo
from app.models.return_transfer import ReturnTransferItem
from app.models.rnd_transfer import RndTransferItem
from app.services.cx_categories import (
    get_category_map,
    normalize_category,
    rev_from_serial,
)
from app.services.slack_cs_parser import parse_slack_cs_messages
from app.services.products import is_bp_product
from app.slack_auth import create_slack_token
from app.utils.query_filters import apply_column_filters
from app.utils.dates import calendar_week

router = APIRouter(dependencies=[Depends(login_required)])
templates = Jinja2Templates(directory="app/templates")

CS_FIELDS = {
    "date_only",
    "cs_started_at",
    "cs_completed_at",
    "hospital_name",
    "rev",
    "serial_no",
    "size",
    "seller",
    "category_main",
    "category_sub",
    "description",
    "exchange_type",
    "transfer_no",
}
CX_FIELDS = {
    "cx_yn",
    "rnd_yn",
    "rnd_team",
    "received_at",
    "processed_at",
    "result_summary",
    "data_loss_type",
    "data_loss_count",
    "issue1",
    "depth1_1",
    "depth1_2",
    "issue2",
    "depth2_1",
    "depth2_2",
}

CX_EXACT_FILTER_HEADERS = {
    "CX", "RnD", "RnD Team", "접수 유형(대)", "접수 유형(소)",
    "교품 여부", "데이터 손실", "불량원인 1", "Depth 1-1",
    "Depth 1-2", "불량원인 2", "Depth 2-1", "Depth 2-2",
}


def cx_filter_mapping():
    return {
        "월": func.strftime("%m", CSCase.date_only),
        "주": CSCase.week,
        "일자": CSCase.date_only,
        "병원명": CSCase.hospital_name,
        "REV 구분": CSCase.rev,
        "식별코드": CSCase.transfer_no,
        "CX": [CSCase.cx_transfer, CXAnalysis.cx_yn],
        "RnD": [CSCase.rnd_transfer, CXAnalysis.rnd_yn],
        "RnD Team": [CSCase.rnd_team, CXAnalysis.rnd_team],
        "접수 유형(대)": CSCase.category_main,
        "접수 유형(소)": CSCase.category_sub,
        "문의사항": CSCase.description,
        "교품 여부": CSCase.exchange_type,
        "S/N": CSCase.serial_no,
        "LOT": CSCase.lot,
        "Size": CSCase.size,
        "접수": [CSCase.received_at, CXAnalysis.received_at],
        "처리": [CSCase.processed_at, CXAnalysis.processed_at],
        "처리 결과": CXAnalysis.result_summary,
        "데이터 손실": case(
            (CXAnalysis.data_loss_type.in_(["general", "non_device_failure"]), "기기 미고장 손실"),
            (CXAnalysis.data_loss_type == "device_failure", "기기 고장 손실"),
            else_="",
        ),
        "손실 건수": CXAnalysis.data_loss_count,
        "불량원인 1": CXAnalysis.issue1,
        "Depth 1-1": CXAnalysis.depth1_1,
        "Depth 1-2": CXAnalysis.depth1_2,
        "불량원인 2": CXAnalysis.issue2,
        "Depth 2-1": CXAnalysis.depth2_1,
        "Depth 2-2": CXAnalysis.depth2_2,
    }


def parse_dt(value, default_year=None):
    if not value:
        return None
    text = str(value).strip().replace("Z", "")
    try:
        fallback_year = int(default_year or datetime.now().year)
    except (TypeError, ValueError):
        fallback_year = datetime.now().year
    if not 2000 <= fallback_year <= 2100:
        fallback_year = datetime.now().year
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if match:
        month, day, hour, minute = map(int, match.groups())
        try:
            return datetime(fallback_year, month, day, hour, minute)
        except ValueError:
            return None
    date_match = re.fullmatch(r"(?:(\d{4})[./-])?(\d{1,2})[./-](\d{1,2})", text)
    if date_match:
        year, month, day = date_match.groups()
        try:
            return datetime(int(year or fallback_year), int(month), int(day))
        except ValueError:
            return None
    return None


def device_status_automation(category_sub):
    category_sub = str(category_sub or "").strip()
    if not category_sub.startswith("기기 상태 이상"):
        return None
    detail_match = re.search(r"\(([^)]+)\)", category_sub)
    detail = detail_match.group(1).strip() if detail_match else ""
    cause = {
        "PMIC OVP": "PMIC",
        "배터리 성능": "배터리 불량",
        "AFE 통신불량": "AFE(센서 불량)",
        "ECG": "AFE(센서 불량)",
        "충전 중 same battery": "배터리 불량",
    }.get(detail, "기타")
    return {
        "exchange_type": "O",
        "result_summary": "교품",
        "issue1": "H/W 이슈",
        "depth1_1": cause,
        "depth1_2": "회수 필요",
    }


def cx_case_order(order):
    """Sort chronologically so placeholder cases stay beside their date."""
    code = func.trim(CSCase.transfer_no)
    numeric = and_(code != "", code.op("GLOB")("[0-9]*"), ~code.op("GLOB")("*[^0-9]*"))
    group = case(
        (or_(CSCase.transfer_no.is_(None), code == ""), 0),
        (numeric, 1),
        else_=2,
    )
    order_fn = asc if order == "asc" else desc
    return (
        order_fn(CSCase.date_only),
        group.asc(),
        order_fn(cast(code, Integer)),
        order_fn(code),
        order_fn(CSCase.id),
    )


def list_context(request, product):
    db = SessionLocal()
    current_year = datetime.now().year
    try:
        selected_year = int(request.query_params.get("year", current_year))
    except (TypeError, ValueError):
        selected_year = current_year
    if not 2000 <= selected_year <= 2100:
        selected_year = current_year
    existing_years = [
        year
        for (year,) in db.query(func.strftime("%Y", CSCase.date_only))
        .filter(CSCase.product == product, CSCase.date_only.isnot(None))
        .distinct()
        .all()
    ]
    available_years = sorted(
        {int(year) for year in existing_years if year}
        | {current_year, selected_year},
        reverse=True,
    )
    page = max(int(request.query_params.get("page", 1)), 1)
    limit = int(request.query_params.get("limit", 50))
    limit = limit if limit in {20, 50, 100} else 50
    keyword = request.query_params.get("keyword", "").strip()
    week_filter = request.query_params.get("week", "").strip()
    review = request.query_params.get("review") == "1"
    order = request.query_params.get("order", "desc")
    order = order if order in {"asc", "desc"} else "desc"
    year_condition = func.strftime("%Y", CSCase.date_only) == str(selected_year)
    if selected_year == current_year:
        year_condition = or_(year_condition, CSCase.date_only.is_(None))
    query = (
        db.query(CSCase)
        .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
        .filter(CSCase.product == product, year_condition)
    )
    if keyword:
        token = f"%{keyword}%"
        query = query.filter(
            or_(
                CSCase.transfer_no.ilike(token),
                CSCase.hospital_name.ilike(token),
                CSCase.serial_no.ilike(token),
                CSCase.description.ilike(token),
            )
        )
    if week_filter:
        try:
            week_number = int(week_filter)
        except ValueError:
            week_number = 0
        if 1 <= week_number <= 53:
            query = query.filter(CSCase.week == week_number)
        else:
            query = query.filter(CSCase.id == -1)
    if review:
        query = query.filter(
            or_(
                and_(CSCase.category_main == "기타", CSCase.category_sub == "기타"),
                CSCase.date_only.is_(None),
            )
        )
    cx_filter_map = cx_filter_mapping()
    query = apply_column_filters(
        query,
        request.query_params,
        cx_filter_map,
        CX_EXACT_FILTER_HEADERS,
    )
    total = query.count()
    total_pages = max((total + limit - 1) // limit, 1)
    page = min(page, total_pages)
    rows = (
        query.order_by(*cx_case_order(order))
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    context = {
        "request": request,
        "product": product,
        "rows": rows,
        "page": page,
        "limit": limit,
        "keyword": keyword,
        "week_filter": week_filter,
        "category_map": get_category_map(product),
        "review": review,
        "order": order,
        "total": total,
        "total_pages": total_pages,
        "selected_year": selected_year,
        "available_years": available_years,
    }
    db.close()
    return context


@router.get("/{product}/cx")
def cx_page(request: Request, product: str):
    context = list_context(request, product)
    db = SessionLocal()
    analyses = {
        item.transfer_no: item
        for item in db.query(CXAnalysis)
        .filter(
            CXAnalysis.transfer_no.in_(
                [r.transfer_no for r in context["rows"] if r.transfer_no]
            )
        )
        .all()
    }
    db.close()
    context["analyses"] = analyses
    context["slack_token"] = create_slack_token(request.session.get("user", ""))
    response = templates.TemplateResponse("renewal_cx.html", context)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@router.get("/{product}/cx/filter-options")
def cx_filter_options(request: Request, product: str, header: str):
    """Return values available after applying every other active CX filter."""
    mapping = cx_filter_mapping()
    expressions = mapping.get(header)
    if header not in CX_EXACT_FILTER_HEADERS or expressions is None:
        raise HTTPException(400, "드롭다운 필터를 지원하지 않는 열입니다.")
    if not isinstance(expressions, (list, tuple)):
        expressions = [expressions]

    db = SessionLocal()
    try:
        query = (
            db.query(CSCase)
            .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
            .filter(CSCase.product == product)
        )
        try:
            selected_year = int(request.query_params.get("year", datetime.now().year))
        except (TypeError, ValueError):
            selected_year = datetime.now().year
        query = query.filter(
            func.strftime("%Y", CSCase.date_only) == str(selected_year)
        )
        keyword = request.query_params.get("keyword", "").strip()
        if keyword:
            token = f"%{keyword}%"
            query = query.filter(
                or_(
                    CSCase.transfer_no.ilike(token),
                    CSCase.hospital_name.ilike(token),
                    CSCase.serial_no.ilike(token),
                    CSCase.description.ilike(token),
                )
            )
        week_filter = request.query_params.get("week", "").strip()
        if week_filter:
            try:
                week_number = int(week_filter)
            except ValueError:
                week_number = 0
            query = query.filter(
                CSCase.week == week_number
                if 1 <= week_number <= 53
                else CSCase.id == -1
            )
        if request.query_params.get("review") == "1":
            query = query.filter(
                CSCase.category_main == "기타", CSCase.category_sub == "기타"
            )
        query = apply_column_filters(
            query,
            request.query_params,
            mapping,
            CX_EXACT_FILTER_HEADERS,
            exclude_headers={header},
        )

        values = set()
        for expression in expressions:
            for (value,) in (
                query.with_entities(cast(expression, String))
                .filter(expression.isnot(None))
                .distinct()
                .all()
            ):
                cleaned = str(value or "").strip()
                if cleaned:
                    values.add(cleaned)
        blank_conditions = [
            or_(expression.is_(None), func.trim(cast(expression, String)) == "")
            for expression in expressions
        ]
        has_blank = query.filter(and_(*blank_conditions)).first() is not None
        return {
            "values": sorted(values),
            "has_blank": has_blank,
        }
    finally:
        db.close()


@router.post("/{product}/cx/create")
def create_case(product: str, year: int = None):
    db = SessionLocal()
    today = datetime.now()
    selected_year = year if year and 2000 <= year <= 2100 else today.year
    case = CSCase(
        product=product,
        date_only=datetime(selected_year, today.month, today.day),
        week=calendar_week(datetime(selected_year, today.month, today.day)),
        exchange_type="X",
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    result = {"id": case.id}
    db.close()
    return result


@router.post("/{product}/cx/case/update/{case_id}")
def update_case(request: Request, product: str, case_id: int, data: dict = Body(...)):
    field = data.get("field")
    if field not in CS_FIELDS:
        raise HTTPException(400, "수정할 수 없는 항목입니다.")
    db = SessionLocal()
    case = (
        db.query(CSCase).filter(CSCase.id == case_id, CSCase.product == product).first()
    )
    if not case:
        db.close()
        raise HTTPException(404, "접수 건을 찾을 수 없습니다.")
    value = data.get("value")
    if field in {"date_only", "cs_started_at", "cs_completed_at"}:
        value = parse_dt(value, data.get("year"))
        if data.get("value") and value is None:
            db.close()
            raise HTTPException(400, "날짜 형식을 확인해 주세요.")
        if field == "date_only":
            case.week = calendar_week(value)
    elif field == "size":
        value = str(value or "").strip()
        if value and not re.fullmatch(r"\d{1,2}", value):
            db.close()
            raise HTTPException(400, "Size는 1~2자리 숫자만 입력할 수 있습니다.")
    elif field == "transfer_no":
        value = str(value or "").strip()
        duplicate = (
            db.query(CSCase.id)
            .filter(
                CSCase.product == product,
                CSCase.transfer_no == value,
                CSCase.id != case_id,
            )
            .first()
            if value
            else None
        )
        if duplicate:
            db.close()
            raise HTTPException(400, "이미 사용 중인 식별코드입니다.")
        if case.transfer_no and case.transfer_no != value:
            analysis = (
                db.query(CXAnalysis)
                .filter(CXAnalysis.transfer_no == case.transfer_no)
                .first()
            )
            if analysis:
                analysis.transfer_no = value or None
    elif field == "serial_no":
        value = str(value or "").strip().upper()
        case.rev = rev_from_serial(value)
        case.lot = value.split("-", 1)[0] if value else None
    setattr(case, field, value or None)
    db.commit()
    result = {"status": "ok"}
    if field == "transfer_no":
        order = request.query_params.get("order", "desc")
        order = order if order in {"asc", "desc"} else "desc"
        try:
            limit = int(request.query_params.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = limit if limit in {20, 50, 100} else 50
        ranked = (
            db.query(
                CSCase.id.label("case_id"),
                func.row_number().over(order_by=cx_case_order(order)).label("position"),
            )
            .filter(
                CSCase.product == product,
                func.strftime("%Y", CSCase.date_only)
                == str(request.query_params.get("year", datetime.now().year)),
            )
            .subquery()
        )
        position = db.query(ranked.c.position).filter(ranked.c.case_id == case_id).scalar()
        result["page"] = ((position - 1) // limit + 1) if position else 1
    if field == "serial_no":
        result.update(
            {
                "serial_no": case.serial_no or "",
                "rev": case.rev or "",
                "lot": case.lot or "",
            }
        )
    db.close()
    return result


@router.delete("/{product}/cx/case/{case_id}")
def delete_case(product: str, case_id: int):
    db = SessionLocal()
    case = (
        db.query(CSCase).filter(CSCase.id == case_id, CSCase.product == product).first()
    )
    if not case:
        db.close()
        raise HTTPException(404, "삭제할 접수 건을 찾을 수 없습니다.")

    code = case.transfer_no
    attachment_paths = []
    if code:
        lab = db.query(LabAnalysis).filter(LabAnalysis.transfer_no == code).first()
        if lab:
            try:
                filenames = json.loads(lab.attachment or "[]")
            except (TypeError, ValueError):
                filenames = [lab.attachment] if lab.attachment else []
            upload_root = (Path.cwd() / "uploads" / "rnd" / "product").resolve()
            for filename in filenames:
                candidate = (upload_root / Path(str(filename)).name).resolve()
                if candidate.parent == upload_root and candidate.is_file():
                    attachment_paths.append(candidate)

        quality = (
            db.query(QualityInspection)
            .filter(QualityInspection.transfer_no == code)
            .first()
        )
        if quality:
            db.query(QualityCellMemo).filter(
                QualityCellMemo.quality_id == quality.id
            ).delete(synchronize_session=False)
            db.delete(quality)
        db.query(LabAnalysis).filter(LabAnalysis.transfer_no == code).delete(
            synchronize_session=False
        )
        db.query(CXAnalysis).filter(CXAnalysis.transfer_no == code).delete(
            synchronize_session=False
        )
        db.query(ExchangeInfo).filter(ExchangeInfo.transfer_no == code).delete(
            synchronize_session=False
        )

    db.query(ReturnTransferItem).filter(ReturnTransferItem.cs_id == case.id).delete(
        synchronize_session=False
    )
    db.query(RndTransferItem).filter(RndTransferItem.cs_id == case.id).delete(
        synchronize_session=False
    )
    db.delete(case)
    db.commit()
    db.close()

    removed_files = 0
    for path in attachment_paths:
        try:
            path.unlink()
            removed_files += 1
        except OSError:
            pass
    return {"status": "ok", "removed_files": removed_files}


@router.post("/{product}/cx/update/{case_id}")
def update_cx(product: str, case_id: int, data: dict = Body(...)):
    field = data.get("field")
    if field not in CX_FIELDS:
        raise HTTPException(400, "수정할 수 없는 항목입니다.")
    db = SessionLocal()
    case = (
        db.query(CSCase).filter(CSCase.id == case_id, CSCase.product == product).first()
    )
    if not case or not case.transfer_no:
        db.close()
        raise HTTPException(400, "식별코드를 먼저 입력해 주세요.")
    analysis = (
        db.query(CXAnalysis).filter(CXAnalysis.transfer_no == case.transfer_no).first()
    )
    if not analysis:
        analysis = CXAnalysis(transfer_no=case.transfer_no)
        db.add(analysis)
    value = data.get("value")
    if isinstance(value, str) and value.strip().lower() in {"none", "null", "nan"}:
        value = None
    if field == "data_loss_type":
        value = str(value or "").strip()
        if value == "general":
            value = "non_device_failure"
        if value not in {"", "non_device_failure", "device_failure"}:
            db.close()
            raise HTTPException(400, "데이터 손실 분류를 확인해 주세요.")
        if value and not case.date_only:
            db.close()
            raise HTTPException(
                400, "데이터 손실을 지정하려면 일자를 먼저 입력해 주세요."
            )
        analysis.data_loss_type = value or None
        analysis.data_loss_count = (
            max(int(analysis.data_loss_count or 1), 1) if value else None
        )
        automatic_reason = (
            db.query(LossReason)
            .filter(
                LossReason.product == product,
                LossReason.transfer_no == case.transfer_no,
            )
            .order_by(LossReason.id.desc())
            .first()
        )
        if value == "device_failure" and not automatic_reason:
            automatic_reason = LossReason(
                product=product,
                year=case.date_only.year,
                week=int(calendar_week(case.date_only)),
                transfer_no=case.transfer_no,
                loss_type="device_failure",
                loss_count=analysis.data_loss_count,
                reason="HW불량",
                created_by="자동 분류",
            )
            db.add(automatic_reason)
        elif (
            value != "device_failure"
            and automatic_reason
            and automatic_reason.reason == "HW불량"
            and automatic_reason.created_by == "자동 분류"
        ):
            db.delete(automatic_reason)
        db.commit()
        count = analysis.data_loss_count
        db.close()
        return {"status": "ok", "data_loss_count": count}
    if field == "data_loss_count":
        if not analysis.data_loss_type:
            db.close()
            raise HTTPException(400, "데이터 손실 분류를 먼저 선택해 주세요.")
        try:
            value = int(value)
        except (TypeError, ValueError):
            db.close()
            raise HTTPException(400, "손실 건수는 숫자로 입력해 주세요.")
        if not 1 <= value <= 999:
            db.close()
            raise HTTPException(400, "손실 건수는 1~999 사이로 입력해 주세요.")
        automatic_reason = (
            db.query(LossReason)
            .filter(
                LossReason.product == product,
                LossReason.transfer_no == case.transfer_no,
                LossReason.reason == "HW불량",
                LossReason.created_by == "자동 분류",
            )
            .first()
        )
        if automatic_reason:
            automatic_reason.loss_count = value
    if field in {"received_at", "processed_at"}:
        if (case.cx_transfer or analysis.cx_yn or "").upper() != "O":
            db.close()
            raise HTTPException(400, "CX 이관 여부가 O인 경우에만 입력할 수 있습니다.")
        value = parse_dt(value, data.get("year"))
        if data.get("value") and value is None:
            db.close()
            raise HTTPException(400, "날짜와 시간을 확인해 주세요.")
    setattr(analysis, field, value or None)
    case_field = {
        "cx_yn": "cx_transfer",
        "rnd_yn": "rnd_transfer",
        "rnd_team": "rnd_team",
        "received_at": "received_at",
        "processed_at": "processed_at",
    }.get(field)
    if case_field:
        setattr(case, case_field, value or None)
    db.commit()
    db.close()
    return {"status": "ok"}


@router.post("/{product}/cx/slack-preview")
async def slack_preview(product: str, data: dict = Body(...)):
    return {"items": parse_slack_cs_messages(data.get("text", ""), product)}


@router.post("/{product}/cx/slack-import")
async def slack_import(product: str, data: dict = Body(...)):
    raw_items = data.get("items") or []
    items = []
    item_positions = {}
    duplicate_codes = []
    for item in raw_items:
        code = str(item.get("transfer_no") or "").strip()
        if not code:
            items.append(item)
            continue
        if code in item_positions:
            items[item_positions[code]] = item
            duplicate_codes.append(code)
        else:
            item_positions[code] = len(items)
            items.append(item)
    update_scope = str(data.get("update_scope") or "hospital").strip().lower()
    if update_scope not in {"hospital", "category", "size", "both"}:
        raise HTTPException(400, "기존 행 반영 범위가 올바르지 않습니다.")
    db = SessionLocal()
    created = 0
    hospital_updated = 0
    category_updated = 0
    size_updated = 0
    skipped = []
    for item in items:
        code = str(item.get("transfer_no") or "").strip()
        if not code:
            skipped.append("식별코드 없음")
            continue
        existing = (
            db.query(CSCase)
            .filter(CSCase.product == product, CSCase.transfer_no == code)
            .first()
        )
        if existing:
            incoming_hospital = str(item.get("hospital_name") or "").strip()
            current_hospital = str(existing.hospital_name or "").strip()
            hospital_is_blank = current_hospital.lower() in {"", "none", "nan"}
            changed = False
            if (
                update_scope in {"hospital", "both"}
                and not is_bp_product(product)
                and hospital_is_blank
                and incoming_hospital
            ):
                existing.hospital_name = incoming_hospital
                hospital_updated += 1
                changed = True
            if update_scope in {"category", "both"}:
                category_main, category_sub = normalize_category(
                    product,
                    str(item.get("category_main") or "").strip(),
                    str(item.get("category_sub") or "").strip(),
                )
                existing.category_main = category_main
                existing.category_sub = category_sub
                existing.description = str(item.get("description") or "").strip()
                automation = device_status_automation(category_sub)
                if automation:
                    existing.exchange_type = automation["exchange_type"]
                    analysis = (
                        db.query(CXAnalysis)
                        .filter(CXAnalysis.transfer_no == code)
                        .first()
                    )
                    if not analysis:
                        analysis = CXAnalysis(transfer_no=code)
                        db.add(analysis)
                    analysis.exchange_type = automation["exchange_type"]
                    for field in ("result_summary", "issue1", "depth1_1", "depth1_2"):
                        setattr(analysis, field, automation[field])
                category_updated += 1
                changed = True
            if update_scope == "size":
                incoming_size = re.sub(r"\D", "", str(item.get("size") or ""))[:2]
                if incoming_size and str(existing.size or "").strip() != incoming_size:
                    existing.size = incoming_size
                    size_updated += 1
                    changed = True
            if not changed:
                skipped.append(code)
            continue
        received = parse_dt(str(item.get("received_at") or "").replace(" ", "T"))
        if not received:
            skipped.append(f"{code} (접수 일시 없음)")
            continue
        serial = str(item.get("serial_no") or "").strip().upper()
        size = re.sub(r"\D", "", str(item.get("size") or ""))[:2]
        rev = rev_from_serial(serial)
        category_main, category_sub = normalize_category(
            product,
            str(item.get("category_main") or "").strip(),
            str(item.get("category_sub") or "").strip(),
        )
        automation = device_status_automation(category_sub)
        case_date = (
            datetime(received.year, received.month, received.day) if received else None
        )
        case = CSCase(
            product=product,
            transfer_no=code,
            hospital_name=(
                str(item.get("hospital_name") or "").strip()
                if not is_bp_product(product)
                else None
            ),
            date_only=case_date,
            received_at=received,
            week=calendar_week(received),
            category_main=category_main,
            category_sub=category_sub,
            description=str(item.get("description") or "").strip(),
            exchange_type=automation["exchange_type"] if automation else None,
            serial_no=serial,
            rev=rev,
            size=size,
            cx_transfer=(
                "O"
                if str(item.get("cx_transfer") or "").strip().upper() == "O"
                else None
            ),
        )
        db.add(case)
        is_cx = str(item.get("cx_transfer") or "").strip().upper() == "O"
        if is_cx or automation:
            analysis = CXAnalysis(
                transfer_no=code,
                cx_yn="O" if is_cx else None,
                received_at=received if is_cx else None,
            )
            if automation:
                analysis.exchange_type = automation["exchange_type"]
                for field in ("result_summary", "issue1", "depth1_1", "depth1_2"):
                    setattr(analysis, field, automation[field])
            db.add(analysis)
        created += 1
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="동일 식별코드의 CX 분석 데이터가 이미 존재합니다. 페이지를 새로고침한 뒤 다시 등록해 주세요.",
        ) from exc
    finally:
        db.close()
    return {
        "status": "ok",
        "created": created,
        "hospital_updated": hospital_updated,
        "category_updated": category_updated,
        "size_updated": size_updated,
        "duplicate_merged": len(duplicate_codes),
        "duplicate_codes": sorted(set(duplicate_codes)),
        "skipped": skipped,
    }