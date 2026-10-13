from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Form, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from datetime import timedelta
from collections import defaultdict
from app.database import SessionLocal, get_db
from app.models.jira_note import JiraIssueNote
from app.models.cs import CSCase
from app.models.quality import QualityInspection
from app.services.lot_service import get_all_lots, get_lot_stats
from app.models.lab import LabAnalysis
from app.models.cx import CXAnalysis
from statistics import median
import numpy as np
from app.utils.jira import (
    get_all_issues,
    get_done_date,
    get_deploy_date,
    START_DATE_MAP,
    DONE_DATE_MAP,
)
import pandas as pd
from fastapi import UploadFile, File
import os
from fastapi.responses import RedirectResponse
from app.models.report_loss import ReportLoss
from app.models.loss_reason import LossReason
from app.database import engine, Base
from app.models.server_downtime import ServerDowntime
from app.models.server_incident import ServerIncident
from fastapi.responses import JSONResponse
from app.services.cx_categories import rev_from_serial
from app.services.process_rev import get_process_rev
from app.services.permissions import account_has_permission
from app.services.products import get_product
from app.utils.dates import calendar_week

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
Base.metadata.create_all(bind=engine)


def calc_change(current, previous):
    diff = current - previous
    if previous == 0:
        return (100 if current > 0 else 0), diff
    percent = round((diff / previous) * 100, 1)
    return percent, diff


UPLOAD_PATH = "data/report.csv"
LOSS_AUTOMATION_START = (1, 1)


def dashboard_week(value):
    """Backward-compatible dashboard alias for the shared calendar week rule."""
    return calendar_week(value)


def is_automatic_loss_week(year, week):
    return (int(year), int(week)) >= LOSS_AUTOMATION_START


def build_automatic_loss_map(rows):
    result = defaultdict(lambda: {"include": 0, "exclude": 0})
    for case_date, _stored_week, loss_type, loss_count in rows:
        if not case_date or loss_type not in {
            "general",
            "non_device_failure",
            "device_failure",
        }:
            continue
        key = (int(case_date.year), dashboard_week(case_date))
        if not is_automatic_loss_week(*key):
            continue
        try:
            count = max(1, int(loss_count or 1))
        except (TypeError, ValueError):
            count = 1
        result[key]["include"] += count
        if loss_type in {"general", "non_device_failure"}:
            result[key]["exclude"] += count
    return result


@router.post("/dashboard/report-upload")
@router.post("/upload-csv", include_in_schema=False)
async def upload_report_excel(file: UploadFile = File(...)):

    try:
        filename = (file.filename or "").lower()
        if not filename.endswith((".xlsx", ".xlsm")):
            raise HTTPException(
                status_code=400,
                detail="Excel 파일(.xlsx, .xlsm)만 업로드할 수 있습니다.",
            )

        os.makedirs("data", exist_ok=True)
        new_df = pd.read_excel(file.file, engine="openpyxl")
        required_columns = ["report_day", "report_cnt"]
        missing_columns = [
            column for column in required_columns if column not in new_df.columns
        ]
        if missing_columns:
            raise HTTPException(
                status_code=400,
                detail=f"필수 열이 없습니다: {', '.join(missing_columns)}",
            )

        new_df = new_df[required_columns].copy()
        new_df["report_day"] = pd.to_datetime(new_df["report_day"], errors="coerce")
        new_df["report_cnt"] = pd.to_numeric(new_df["report_cnt"], errors="coerce")
        if new_df[required_columns].isna().any().any():
            raise HTTPException(
                status_code=400,
                detail="report_day 또는 report_cnt에 비어 있거나 잘못된 값이 있습니다.",
            )
        if (new_df["report_cnt"] < 0).any():
            raise HTTPException(
                status_code=400,
                detail="report_cnt는 0 이상의 숫자여야 합니다.",
            )
        if (new_df["report_cnt"] % 1 != 0).any():
            raise HTTPException(
                status_code=400,
                detail="report_cnt는 정수여야 합니다.",
            )

        new_df["report_day"] = new_df["report_day"].dt.strftime("%Y-%m-%d")
        new_df["report_cnt"] = new_df["report_cnt"].astype(int)
        new_df = new_df.drop_duplicates(subset=["report_day"], keep="last")

        if os.path.exists(UPLOAD_PATH):
            old_df = pd.read_csv(UPLOAD_PATH)
            old_df = old_df[required_columns]
            old_df["report_day"] = pd.to_datetime(
                old_df["report_day"], errors="coerce"
            ).dt.strftime("%Y-%m-%d")
            old_df = old_df[~old_df["report_day"].isin(new_df["report_day"])]
            df = pd.concat([old_df, new_df], ignore_index=True)

        else:
            df = new_df

        df = df.sort_values("report_day").reset_index(drop=True)
        df.to_csv(UPLOAD_PATH, index=False)

        return RedirectResponse(url="/cart_bp_pro/dashboard?page=3", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get-loss-data")
def get_loss_data():

    db = SessionLocal()
    data = db.query(ReportLoss).all()
    db.close()

    return [
        {
            "year": d.year,
            "week": d.week,
            "lost_include": d.lost_include,
            "lost_exclude": d.lost_exclude,
        }
        for d in data
    ]


@router.post("/save-loss")
async def save_loss_api(request: Request):
    data = await request.json()
    year = int(data["year"])
    week = int(data["week"])
    if is_automatic_loss_week(year, week):
        raise HTTPException(
            status_code=400,
            detail="데이터 손실은 CX 분석 페이지에서 자동 집계됩니다.",
        )
    db = SessionLocal()
    existing = (
        db.query(ReportLoss)
        .filter(ReportLoss.year == year, ReportLoss.week == week)
        .first()
    )

    if existing:
        if "lost_include" in data:
            existing.lost_include = int(data["lost_include"])

        if "lost_exclude" in data:
            existing.lost_exclude = int(data["lost_exclude"])

    else:
        db.add(
            ReportLoss(
                year=year,
                week=week,
                lost_include=int(data.get("lost_include", 0)),
                lost_exclude=int(data.get("lost_exclude", 0)),
            )
        )

    db.commit()
    db.close()

    return {"status": "ok"}


@router.post("/{product}/dashboard/loss-reasons")
async def create_loss_reason(product: str, request: Request):
    data = await request.json()
    try:
        year = int(data.get("year") or datetime.now().year)
        week = int(data.get("week"))
        loss_count = int(data.get("loss_count") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "연도, 주차, 손실 건수는 숫자로 입력해 주세요.")
    if not 1 <= week <= 53:
        raise HTTPException(400, "주차는 1~53 사이로 입력해 주세요.")
    if not 1 <= loss_count <= 99999:
        raise HTTPException(400, "손실 건수는 1~99,999 사이로 입력해 주세요.")
    loss_type = str(data.get("loss_type") or "").strip()
    if loss_type not in {"device_failure", "non_device_failure"}:
        raise HTTPException(400, "손실 구분을 선택해 주세요.")
    reason = str(data.get("reason") or "").strip()
    detail = str(data.get("detail") or "").strip()
    transfer_no = str(data.get("transfer_no") or "").strip()
    if not reason:
        raise HTTPException(400, "손실 사유를 입력해 주세요.")
    if len(reason) > 120 or len(detail) > 2000:
        raise HTTPException(
            400, "손실 사유는 120자, 상세 내용은 2,000자 이내로 입력해 주세요."
        )

    db = SessionLocal()
    try:
        if transfer_no and not (
            db.query(CSCase.id)
            .filter(CSCase.product == product, CSCase.transfer_no == transfer_no)
            .first()
        ):
            raise HTTPException(400, "현재 제품에서 식별코드를 찾을 수 없습니다.")
        item = None
        if transfer_no:
            item = (
                db.query(LossReason)
                .filter(
                    LossReason.product == product,
                    LossReason.transfer_no == transfer_no,
                )
                .order_by(LossReason.id.desc())
                .first()
            )
        required_action = "edit" if item else "create"
        if not account_has_permission(db, request.session.get("user_id"), "dashboard", required_action):
            raise HTTPException(403, f"대시보드 {required_action} 권한이 없습니다.")
        if item:
            item.year = year
            item.week = week
            item.loss_type = loss_type
            item.loss_count = loss_count
            item.reason = reason
            item.detail = detail or None
            item.created_by = str(request.session.get("user") or "사용자")
        else:
            item = LossReason(
                product=product,
                year=year,
                week=week,
                transfer_no=transfer_no or None,
                loss_type=loss_type,
                loss_count=loss_count,
                reason=reason,
                detail=detail or None,
                created_by=str(request.session.get("user") or "사용자"),
            )
            db.add(item)
        db.commit()
        return {"status": "ok", "id": item.id}
    finally:
        db.close()


@router.delete("/{product}/dashboard/loss-reasons/{reason_id}")
def delete_loss_reason(product: str, reason_id: int):
    db = SessionLocal()
    try:
        item = (
            db.query(LossReason)
            .filter(LossReason.id == reason_id, LossReason.product == product)
            .first()
        )
        if not item:
            raise HTTPException(404, "손실 사유 이력을 찾을 수 없습니다.")
        db.delete(item)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/save-uptime")
async def save_uptime(request: Request):
    data = await request.json()
    product = str(data.get("product") or "").strip()
    try:
        year = int(data.get("year") or datetime.now().year)
        week = int(data.get("week"))
        downtime = int(data.get("downtime", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "연도, 주차, 중단 시간은 숫자로 입력해 주세요.")
    reason = str(data.get("reason") or "").strip()
    if not product:
        raise HTTPException(400, "제품 정보가 없습니다.")
    if not 1 <= week <= 53:
        raise HTTPException(400, "주차는 1~53 사이로 입력해 주세요.")
    if not 0 <= downtime <= 10080:
        raise HTTPException(400, "중단 시간은 0~10,080분 사이로 입력해 주세요.")
    if len(reason) > 2000:
        raise HTTPException(400, "서버 중단 사유는 2,000자 이내로 입력해 주세요.")

    db = SessionLocal()
    try:
        row = (
            db.query(ServerDowntime)
            .filter(
                ServerDowntime.product == product,
                ServerDowntime.week == week,
                or_(ServerDowntime.year == year, ServerDowntime.year.is_(None)),
            )
            .order_by(ServerDowntime.year.desc(), ServerDowntime.id.desc())
            .first()
        )
        if row:
            row.year = year
            row.downtime_minutes = downtime
            row.reason = reason or None
            row.updated_at = datetime.now()
        else:
            row = ServerDowntime(
                product=product,
                year=year,
                week=week,
                downtime_minutes=downtime,
                reason=reason or None,
                updated_at=datetime.now(),
            )
            db.add(row)
        db.commit()
        return JSONResponse({"status": "ok"})
    finally:
        db.close()


def parse_server_incident(data):
    try:
        occurred_at = datetime.strptime(str(data.get("occurred_at") or ""), "%Y-%m-%dT%H:%M")
        duration = int(data.get("duration_minutes"))
        cs_count = int(data.get("cs_count"))
    except (TypeError, ValueError):
        raise HTTPException(400, "발생 일시, 중단 시간, CS 건수를 확인해 주세요.")
    if not 0 <= duration <= 10080 or not 0 <= cs_count <= 99999:
        raise HTTPException(400, "중단 시간은 0~10,080분, CS 건수는 0~99,999건으로 입력해 주세요.")
    values = {
        "occurred_at": occurred_at,
        "duration_minutes": duration,
        "cs_count": cs_count,
    }
    for field, limit in (
        ("reason", 2000), ("cs_details", 4000),
        ("rnd_response", 4000), ("business_response", 4000),
    ):
        value = str(data.get(field) or "").strip()
        if len(value) > limit:
            raise HTTPException(400, f"{field} 입력 길이를 확인해 주세요.")
        values[field] = value or None
    for field in ("rnd_status", "business_status"):
        value = str(data.get(field) or "대응 예정").strip()
        if value not in {"대응 예정", "진행 중", "대응 완료"}:
            raise HTTPException(400, "대응 상태를 확인해 주세요.")
        values[field] = value
    return values


@router.post("/{product}/dashboard/uptime-incidents")
async def create_server_incident(product: str, request: Request):
    if not get_product(product):
        raise HTTPException(404, "등록되지 않은 운영 제품입니다.")
    values = parse_server_incident(await request.json())
    db = SessionLocal()
    try:
        record = ServerIncident(product=product, **values)
        db.add(record)
        db.commit()
        return {
            "status": "ok",
            "id": record.id,
            "week_key": f"{record.occurred_at.year}-{dashboard_week(record.occurred_at)}",
        }
    finally:
        db.close()


@router.put("/{product}/dashboard/uptime-incidents/{incident_id}")
async def update_server_incident(product: str, incident_id: int, request: Request):
    values = parse_server_incident(await request.json())
    db = SessionLocal()
    try:
        record = db.query(ServerIncident).filter_by(id=incident_id, product=product).first()
        if not record:
            raise HTTPException(404, "서버 중단 기록을 찾을 수 없습니다.")
        for field, value in values.items():
            setattr(record, field, value)
        record.updated_at = datetime.now()
        db.commit()
        return {
            "status": "ok",
            "week_key": f"{record.occurred_at.year}-{dashboard_week(record.occurred_at)}",
        }
    finally:
        db.close()


@router.delete("/{product}/dashboard/uptime-incidents/{incident_id}")
def delete_server_incident(product: str, incident_id: int):
    db = SessionLocal()
    try:
        record = db.query(ServerIncident).filter_by(id=incident_id, product=product).first()
        if not record:
            raise HTTPException(404, "서버 중단 기록을 찾을 수 없습니다.")
        db.delete(record)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/{product}/dashboard")
def dashboard(request: Request, product: str, page: int = 1):
    from app.services.products import get_product

    product_config = get_product(product)
    if not product_config:
        raise HTTPException(status_code=404, detail="등록되지 않은 운영 제품입니다.")
    product_name = product_config["name"]

    db = SessionLocal()
    case_date_expr = func.coalesce(CSCase.date_only, CSCase.received_at)

    now = datetime.now()
    this_month = now.strftime("%m")
    last_month_date = now.replace(day=1) - timedelta(days=1)
    last_month = last_month_date.strftime("%m")
    year = now.strftime("%Y")
    available_years = [
        int(value)
        for (value,) in (
            db.query(func.strftime("%Y", case_date_expr))
            .filter(CSCase.product == product, case_date_expr.isnot(None))
            .distinct()
            .order_by(func.strftime("%Y", case_date_expr).desc())
            .all()
        )
        if value and str(value).isdigit()
    ]
    if not available_years:
        available_years = [now.year]
    requested_year = request.query_params.get("year", str(available_years[0]))
    dashboard_all_years = requested_year == "all"
    try:
        dashboard_year = available_years[0] if dashboard_all_years else int(requested_year)
    except (TypeError, ValueError):
        dashboard_year = available_years[0]
    if dashboard_year not in available_years:
        dashboard_year = available_years[0]
    dashboard_year_param = "all" if dashboard_all_years else str(dashboard_year)
    dashboard_start = datetime(
        min(available_years) if dashboard_all_years else dashboard_year, 1, 1
    )
    dashboard_end = datetime(
        (max(available_years) if dashboard_all_years else dashboard_year) + 1, 1, 1
    )
    dashboard_context = {
        "dashboard_year": dashboard_year,
        "dashboard_year_param": dashboard_year_param,
        "dashboard_all_years": dashboard_all_years,
        "dashboard_years": available_years,
    }

    def month_count(model, column, product, condition=None):

        q_this = db.query(func.count(model.id))
        q_last = db.query(func.count(model.id))

        if model != CSCase:
            q_this = q_this.join(CSCase, model.transfer_no == CSCase.transfer_no)
            q_last = q_last.join(CSCase, model.transfer_no == CSCase.transfer_no)
            product_filter = CSCase.product == product
        else:
            product_filter = model.product == product

        q_this = q_this.filter(
            func.strftime("%Y-%m", column) == now.strftime("%Y-%m"), product_filter
        )

        q_last = q_last.filter(
            func.strftime("%Y-%m", column) == last_month_date.strftime("%Y-%m"),
            product_filter,
        )

        if condition is not None:
            q_this = q_this.filter(condition)
            q_last = q_last.filter(condition)

        return q_this.scalar(), q_last.scalar()

    week_data = defaultdict(list)

    cases = (
        db.query(
            CSCase.received_at,
            CSCase.processed_at,
            CXAnalysis.received_at,
            CXAnalysis.processed_at,
        )
        .outerjoin(CXAnalysis, CXAnalysis.transfer_no == CSCase.transfer_no)
        .filter(
            CSCase.product == product,
            case_date_expr >= dashboard_start,
            case_date_expr < dashboard_end,
        )
        .all()
    )

    for (
        legacy_received,
        legacy_processed,
        cx_received,
        cx_processed,
    ) in cases:
        received = legacy_received or cx_received
        processed = legacy_processed or cx_processed
        if not received or not processed:
            continue
        diff = (processed - received).total_seconds() / 60
        week = dashboard_week(received)
        if diff >= 0 and week:
            week_data[week].append(diff)

    current_week = dashboard_week(now)
    last_iso_week = datetime(dashboard_year, 12, 28).isocalendar().week
    display_week = current_week - 1 if dashboard_year == now.year else last_iso_week

    weeks = list(range(1, display_week + 1))

    week_total_minutes = 7 * 24 * 60  # 10080

    rows = (
        db.query(ServerDowntime)
        .filter(
            ServerDowntime.product == product,
            or_(
                ServerDowntime.year == dashboard_year,
                ServerDowntime.year.is_(None),
            ),
        )
        .order_by(ServerDowntime.year.asc(), ServerDowntime.id.asc())
        .all()
    )

    rows_by_week = {}
    for row in rows:
        previous = rows_by_week.get(row.week)
        if previous is None or row.year == dashboard_year:
            rows_by_week[row.week] = row

    downtime_data = {
        week: int(row.downtime_minutes or 0) for week, row in rows_by_week.items()
    }
    downtime_reasons = {week: (row.reason or "") for week, row in rows_by_week.items()}
    downtime_entries = [
        {
            "week": week,
            "downtime": downtime_data[week],
            "reason": downtime_reasons[week],
        }
        for week in sorted(rows_by_week, reverse=True)
        if downtime_data[week] or downtime_reasons[week]
    ]

    downtime_map = downtime_data

    weekly_uptime = []
    cumulative_uptime = []

    total_downtime = 0

    for i, w in enumerate(weeks):

        downtime = downtime_data.get(w, 0)

        uptime = (1 - downtime / week_total_minutes) * 100
        weekly_uptime.append(round(uptime, 3))

        total_downtime += downtime

        total_minutes = (i + 1) * week_total_minutes

        cumulative = (1 - total_downtime / total_minutes) * 100
        cumulative_uptime.append(round(cumulative, 4))

    quarter_uptime = {}

    quarters = {
        "1Q": range(1, 14),
        "2Q": range(14, 27),
        "3Q": range(27, 40),
        "4Q": range(40, 53),
    }

    for q_name, q_weeks in quarters.items():

        q_labels = []
        q_cumulative = []

        total_downtime = 0

        valid_weeks = [w for w in q_weeks if w <= display_week]

        q_weekly = []

        for i, w in enumerate(valid_weeks):

            q_labels.append(w)

            downtime = downtime_data.get(w, 0)

            weekly = (1 - downtime / week_total_minutes) * 100

            q_weekly.append(round(weekly, 4))

            total_downtime += downtime

            total_minutes = (i + 1) * week_total_minutes

            cumulative = (1 - total_downtime / total_minutes) * 100

            q_cumulative.append(round(cumulative, 4))

        quarter_uptime[q_name] = {
            "weeks": q_labels,
            "cumulative": q_cumulative,
            "weekly": q_weekly,
        }

    cx_3q = []
    sla_rate = []

    max_time = []
    median_time = []
    q3_time = []

    for w in weeks:

        times = week_data.get(w, [])

        if not times:
            cx_3q.append(None)
            sla_rate.append(None)

            max_time.append(None)
            median_time.append(None)
            q3_time.append(None)
            continue

        arr = np.array(times)

        p50 = np.percentile(arr, 50)
        p75 = np.percentile(arr, 75)

        cx_3q.append(round(p75, 2))

        target_time = 8 if product == "cart_bp_pro" else 60
        sla = np.sum(arr <= target_time) / len(arr) * 100
        sla_rate.append(round(sla, 2))

        max_time.append(round(arr.max(), 2))
        median_time.append(round(p50, 2))
        q3_time.append(round(p75, 2))

    UPLOAD_PATH = "data/report.csv"

    week_labels = []
    week_counts = []
    month_labels = []
    month_counts = []
    df = pd.DataFrame(columns=["report_day", "report_cnt", "month", "week"])
    week_grouped = pd.DataFrame(columns=["week", "report_cnt"])

    try:
        if os.path.exists(UPLOAD_PATH):

            df = pd.read_csv(UPLOAD_PATH)

            if "report_day" not in df.columns or "report_cnt" not in df.columns:
                raise Exception("CSV 컬럼 오류 (report_day, report_cnt 필요)")

            df["report_day"] = pd.to_datetime(df["report_day"])

            df["month"] = df["report_day"].dt.month

            df["calendar_year"] = df["report_day"].dt.year.astype(int)
            df["week"] = df["report_day"].map(dashboard_week).astype(int)

            if not dashboard_all_years:
                df = df[df["calendar_year"] == dashboard_year].copy()

            week_grouped = (
                df.groupby("week")["report_cnt"].sum().reset_index().sort_values("week")
            )

            week_labels = week_grouped["week"].astype(int).astype(str).tolist()
            week_counts = week_grouped["report_cnt"].astype(int).tolist()

            month_grouped = (
                df.groupby("month")["report_cnt"]
                .sum()
                .reset_index()
                .sort_values("month")
            )

            month_labels = [f"{int(m)}월" for m in month_grouped["month"]]
            month_counts = month_grouped["report_cnt"].astype(int).tolist()

    except Exception as e:
        print("CSV 처리 오류:", e)

    automatic_loss_rows = (
        db.query(
            CSCase.date_only,
            CSCase.week,
            CXAnalysis.data_loss_type,
            CXAnalysis.data_loss_count,
        )
        .join(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
        .filter(
            CSCase.product == product,
            CSCase.date_only.isnot(None),
            CXAnalysis.data_loss_type.in_(
                ["general", "non_device_failure", "device_failure"]
            ),
        )
        .all()
    )
    automatic_loss_map = build_automatic_loss_map(automatic_loss_rows)

    now_year = dashboard_year

    week_lost_include = []
    week_lost_exclude = []
    week_loss_sources = []

    for w in week_labels:
        week = int(w)
        key = (now_year, week)

        week_lost_include.append(automatic_loss_map[key]["include"])
        week_lost_exclude.append(automatic_loss_map[key]["exclude"])
        week_loss_sources.append("auto")

    month_lost_include = [0] * len(month_labels)
    month_lost_exclude = [0] * len(month_labels)
    month_to_index = {
        int(label.removesuffix("월")): index for index, label in enumerate(month_labels)
    }

    for i in range(len(week_grouped)):

        week = int(week_grouped.iloc[i]["week"])
        key = (now_year, week)

        week_dates = df[df["week"] == week]

        if not week_dates.empty:
            month = int(week_dates.iloc[0]["month"])
            month_index = month_to_index.get(month)

            if month_index is not None:
                month_lost_include[month_index] += week_lost_include[i]
                month_lost_exclude[month_index] += week_lost_exclude[i]

    total_count = sum(month_counts)
    total_lost_include = sum(month_lost_include)
    total_lost_exclude = sum(month_lost_exclude)

    def calc_rate(lost, total):
        return round((lost / total * 100), 3) if total else 0

    loss_rate_include = calc_rate(total_lost_include, total_count)
    loss_rate_exclude = calc_rate(total_lost_exclude, total_count)

    loss_target = 0.025

    if page == 3 and product_config["profile"] != "pro":
        db.close()
        raise HTTPException(status_code=404, detail="Not Found")

    if page == 4 and not product_config["jira_project"]:
        db.close()
        raise HTTPException(status_code=404, detail="Jira 프로젝트가 연결되지 않았습니다.")

    if page == 1:
        start = request.query_params.get("start")
        end = request.query_params.get("end")

        compare_start = request.query_params.get("compare_start")
        compare_end = request.query_params.get("compare_end")

        is_filtered = bool(start and end)
        is_compare = bool(compare_start and compare_end)

        if is_filtered:
            start_date = datetime.strptime(start, "%Y-%m-%d")
            end_date = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
        else:
            start_date = end_date = None

        if is_compare:
            c_start_date = datetime.strptime(compare_start, "%Y-%m-%d")
            c_end_date = datetime.strptime(compare_end, "%Y-%m-%d") + timedelta(days=1)
        else:
            c_start_date = c_end_date = None

        def count_data(model, column, start=None, end=None, *conditions):

            q = db.query(func.count(model.id))

            if model != CSCase:
                q = q.join(CSCase, model.transfer_no == CSCase.transfer_no)
                q = q.filter(CSCase.product == product)
            else:
                q = q.filter(model.product == product)

            if start and end:
                q = q.filter(column >= start, column < end)
            else:
                q = q.filter(
                    case_date_expr >= dashboard_start,
                    case_date_expr < dashboard_end,
                )

            for cond in conditions:
                q = q.filter(cond)

            return q.scalar()

        def count_cx(start=None, end=None):
            q = (
                db.query(func.count(CSCase.id))
                .outerjoin(CXAnalysis, CXAnalysis.transfer_no == CSCase.transfer_no)
                .filter(
                    CSCase.product == product,
                    func.upper(func.coalesce(CXAnalysis.cx_yn, CSCase.cx_transfer, ""))
                    == "O",
                )
            )
            if start and end:
                q = q.filter(case_date_expr >= start, case_date_expr < end)
            else:
                q = q.filter(
                    case_date_expr >= dashboard_start,
                    case_date_expr < dashboard_end,
                )
            return q.scalar()

        total_cs = count_data(CSCase, case_date_expr, start_date, end_date)

        cx_done = count_cx(start_date, end_date)

        total_exchange = count_data(
            CSCase,
            case_date_expr,
            start_date,
            end_date,
            func.lower(CSCase.exchange_type).in_(["o", "cradle"]),
        )

        if is_compare:

            prev_cs = count_data(CSCase, case_date_expr, c_start_date, c_end_date)
            prev_cx = count_cx(c_start_date, c_end_date)
            prev_exchange = count_data(
                CSCase,
                case_date_expr,
                c_start_date,
                c_end_date,
                func.lower(CSCase.exchange_type).in_(["o", "cradle"]),
            )

            cs_change, cs_diff = calc_change(total_cs, prev_cs)
            cx_change, cx_diff = calc_change(cx_done, prev_cx)
            exchange_change, exchange_diff = calc_change(total_exchange, prev_exchange)

        else:
            cs_change = cx_change = exchange_change = None
            cs_diff = cx_diff = exchange_diff = None

        if is_filtered:
            context_label = f"{start} ~ {end}"
        else:
            context_label = "전체 연도" if dashboard_all_years else f"{dashboard_year}년 전체"

        if is_compare:
            compare_label = f"{compare_start} ~ {compare_end}"
        else:
            compare_label = None

        db.close()

        return templates.TemplateResponse(
            "dashboard/dashboard_page1.html",
            {
                "request": request,
                **dashboard_context,
                "page": 1,
                "product": product_name,
                "product_code": product,
                "total_cs": total_cs,
                "cx_done": cx_done,
                "total_exchange": total_exchange,
                "cs_change": cs_change,
                "cs_diff": cs_diff,
                "cx_change": cx_change,
                "cx_diff": cx_diff,
                "exchange_change": exchange_change,
                "exchange_diff": exchange_diff,
                "context_label": context_label,
                "compare_label": compare_label,
                "weeks": weeks,
                "cx_3q": cx_3q,
                "sla_rate": sla_rate,
                "max_time": max_time,
                "median_time": median_time,
                "q3_time": q3_time,
                "weekly_uptime": weekly_uptime,
                "quarter_uptime": quarter_uptime,
                "cumulative_uptime": cumulative_uptime,
                "downtime_map": downtime_map,
            },
        )

    if page == 6:
        downtime_values = [
            downtime_data.get(w, 0) for w in weeks if downtime_data.get(w, 0) > 0
        ]
        downtime_total = sum(downtime_values)
        downtime_average = (
            round(downtime_total / len(downtime_values), 1) if downtime_values else 0
        )
        incident_year = dashboard_year
        incident_query = db.query(ServerIncident).filter(
            ServerIncident.product == product
        )
        if not dashboard_all_years:
            incident_query = incident_query.filter(
                ServerIncident.occurred_at >= datetime(incident_year, 1, 1),
                ServerIncident.occurred_at < datetime(incident_year + 1, 1, 1),
            )
        incidents = incident_query.order_by(
            ServerIncident.occurred_at.desc(), ServerIncident.id.desc()
        ).all()
        incident_groups = []
        for incident in incidents:
            key = (incident.occurred_at.year, dashboard_week(incident.occurred_at))
            if not incident_groups or incident_groups[-1]["key"] != key:
                incident_year_value, incident_week = key
                incident_groups.append({
                    "key": key,
                    "key_string": f"{incident_year_value}-{incident_week}",
                    "year": incident_year_value,
                    "week": incident_week,
                    "rows": [],
                    "cs_total": 0,
                    "duration_total": 0,
                })
            incident_groups[-1]["rows"].append(incident)
            incident_groups[-1]["cs_total"] += incident.cs_count or 0
            incident_groups[-1]["duration_total"] += incident.duration_minutes or 0
        requested_week = request.query_params.get("incident_week", "")
        incident_selected_week = next(
            (
                requested_week for group in incident_groups
                if requested_week == group["key_string"]
            ),
            incident_groups[0]["key_string"] if incident_groups else "",
        )
        can_create_incident = account_has_permission(db, request.session.get("user_id"), "dashboard", "create")
        can_edit_incident = account_has_permission(db, request.session.get("user_id"), "dashboard", "edit")
        can_delete_incident = account_has_permission(db, request.session.get("user_id"), "dashboard", "delete")
        db.close()
        return templates.TemplateResponse(
            "dashboard/dashboard_page6.html",
            {
                "request": request,
                **dashboard_context,
                "page": 6,
                "product": product_name,
                "product_code": product,
                "weeks": weeks,
                "weekly_uptime": weekly_uptime,
                "cumulative_uptime": cumulative_uptime,
                "downtime_map": downtime_map,
                "downtime_reasons": downtime_reasons,
                "downtime_entries": downtime_entries,
                "downtime_total": downtime_total,
                "downtime_average": downtime_average,
                "downtime_week_count": len(downtime_values),
                "downtime_week_max": max(downtime_values, default=0),
                "incident_year": incident_year,
                "incident_groups": incident_groups,
                "incident_selected_week": incident_selected_week,
                "can_create_incident": can_create_incident,
                "can_edit_incident": can_edit_incident,
                "can_delete_incident": can_delete_incident,
                "quarter_uptime": quarter_uptime,
                "kpi_year": cumulative_uptime[-1] if cumulative_uptime else 100,
                "kpi_1q": (
                    quarter_uptime["1Q"]["cumulative"][-1]
                    if quarter_uptime["1Q"]["cumulative"]
                    else None
                ),
                "kpi_2q": (
                    quarter_uptime["2Q"]["cumulative"][-1]
                    if quarter_uptime["2Q"]["cumulative"]
                    else None
                ),
                "kpi_3q": (
                    quarter_uptime["3Q"]["cumulative"][-1]
                    if quarter_uptime["3Q"]["cumulative"]
                    else None
                ),
                "kpi_4q": (
                    quarter_uptime["4Q"]["cumulative"][-1]
                    if quarter_uptime["4Q"]["cumulative"]
                    else None
                ),
            },
        )
    if page == 2:
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        compare_start_str = request.query_params.get("compare_start")
        compare_end_str = request.query_params.get("compare_end")

        def parse_date(value, fallback):
            try:
                return datetime.strptime(value, "%Y-%m-%d") if value else fallback
            except ValueError:
                return fallback

        default_period_end = now if dashboard_year == now.year else dashboard_end - timedelta(days=1)
        start = parse_date(start_str, dashboard_start)
        end = parse_date(end_str, default_period_end)
        if start > end:
            start, end = end, start
        compare_start = parse_date(compare_start_str, None)
        compare_end = parse_date(compare_end_str, None)
        if compare_start and compare_end and compare_start > compare_end:
            compare_start, compare_end = compare_end, compare_start

        def cohort_rows(period_start, period_end):
            if not period_start or not period_end:
                return []
            return (
                db.query(CSCase, QualityInspection, LabAnalysis)
                .outerjoin(
                    QualityInspection,
                    QualityInspection.transfer_no == CSCase.transfer_no,
                )
                .outerjoin(LabAnalysis, LabAnalysis.transfer_no == CSCase.transfer_no)
                .filter(
                    CSCase.product == product,
                    func.lower(func.trim(CSCase.exchange_type)).in_(["o", "cradle"]),
                    case_date_expr >= period_start,
                    case_date_expr < period_end + timedelta(days=1),
                )
                .all()
            )

        def cohort_metrics(rows):
            exchanged = len(rows)
            recovered = sum(cs.return_at is not None for cs, _, _ in rows)
            waiting = exchanged - recovered
            quality_registered = sum(
                cs.return_at is not None and quality is not None
                for cs, quality, _ in rows
            )
            analysis_done = sum(
                cs.return_at is not None
                and quality is not None
                and lab is not None
                and lab.analysis_completed_date is not None
                for cs, quality, lab in rows
            )
            return exchanged, recovered, waiting, quality_registered, analysis_done

        rows = cohort_rows(start, end)
        (
            current_exchange,
            current_recovery,
            current_waiting,
            current_quality,
            total_done,
        ) = cohort_metrics(rows)

        exchange_only_count = sum(
            (cs.exchange_type or "").strip().lower() == "o" for cs, _, _ in rows
        )
        cradle_exchange_count = current_exchange - exchange_only_count
        quality_pending_count = max(current_recovery - current_quality, 0)
        analysis_pending_count = max(current_quality - total_done, 0)

        def percent(part, whole):
            return round(part / whole * 100, 1) if whole else 0

        recovery_rate = percent(current_recovery, current_exchange)
        quality_rate = percent(current_quality, current_recovery)
        quality_overall_rate = percent(current_quality, current_exchange)
        analysis_rate = percent(total_done, current_quality)
        end_to_end_rate = percent(total_done, current_exchange)

        waiting_age_bucket_labels = ["0~3일", "4~7일", "8~14일", "15~30일", "31일 이상"]
        waiting_age_bucket_counts = [0, 0, 0, 0, 0]
        waiting_ages = []
        for cs, _, _ in rows:
            case_date = cs.date_only or cs.received_at
            if cs.return_at is not None or case_date is None:
                continue
            age_reference = min(end.date(), now.date())
            age = max((age_reference - case_date.date()).days, 0)
            waiting_ages.append(age)
            if age <= 3:
                waiting_age_bucket_counts[0] += 1
            elif age <= 7:
                waiting_age_bucket_counts[1] += 1
            elif age <= 14:
                waiting_age_bucket_counts[2] += 1
            elif age <= 30:
                waiting_age_bucket_counts[3] += 1
            else:
                waiting_age_bucket_counts[4] += 1
        long_waiting_count = sum(age >= 15 for age in waiting_ages)
        shortest_waiting_days = min(waiting_ages) if waiting_ages else None
        oldest_waiting_days = max(waiting_ages) if waiting_ages else None

        if compare_start and compare_end:
            previous = cohort_metrics(cohort_rows(compare_start, compare_end))
            changes = [
                calc_change(current, before)
                for current, before in zip(
                    [
                        current_exchange,
                        current_recovery,
                        current_waiting,
                        current_quality,
                        total_done,
                    ],
                    previous,
                )
            ]
            (
                (exchange_change, exchange_diff),
                (recovery_change, recovery_diff),
                (waiting_change, waiting_diff),
                (quality_change, quality_diff),
                (done_change, done_diff),
            ) = changes
        else:
            exchange_change = recovery_change = waiting_change = quality_change = (
                done_change
            ) = None
            exchange_diff = recovery_diff = waiting_diff = quality_diff = done_diff = (
                None
            )

        current_label = (
            f"{start.strftime('%Y.%m.%d')} ~ {end.strftime('%Y.%m.%d')} 접수분"
        )
        compare_label = (
            f"{compare_start.strftime('%Y.%m.%d')} ~ {compare_end.strftime('%Y.%m.%d')} 접수분"
            if compare_start and compare_end
            else ""
        )

        month_keys = []
        cursor = start.replace(day=1)
        last_month = end.replace(day=1)
        while cursor <= last_month:
            month_keys.append(cursor.strftime("%Y-%m"))
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

        monthly = {
            key: {
                "exchange": 0,
                "cradle": 0,
                "total": 0,
                "recovered": 0,
                "quality": 0,
                "done": 0,
            }
            for key in month_keys
        }
        lead_exchange_return = []

        for cs, quality, lab in rows:
            case_date = cs.date_only or cs.received_at
            if not case_date:
                continue
            month_key = case_date.strftime("%Y-%m")
            if month_key not in monthly:
                continue
            monthly[month_key]["total"] += 1
            if (cs.exchange_type or "").strip().lower() == "o":
                monthly[month_key]["exchange"] += 1
            else:
                monthly[month_key]["cradle"] += 1
            if cs.return_at:
                monthly[month_key]["recovered"] += 1
                lead_days = (cs.return_at.date() - case_date.date()).days
                if lead_days >= 0:
                    lead_exchange_return.append(lead_days)
            if cs.return_at and quality:
                monthly[month_key]["quality"] += 1
            if cs.return_at and quality and lab and lab.analysis_completed_date:
                monthly[month_key]["done"] += 1

        month_labels = [
            datetime.strptime(key, "%Y-%m").strftime("%y년 %m월") for key in month_keys
        ]
        monthly_exchange_chart = [monthly[key]["exchange"] for key in month_keys]
        monthly_cradle_chart = [monthly[key]["cradle"] for key in month_keys]
        monthly_waiting_counts = [
            monthly[key]["total"] - monthly[key]["recovered"] for key in month_keys
        ]

        stage_labels = ["교품 접수", "회수 완료", "품질 분석 등록", "분석 완료"]
        stage_counts = [current_exchange, current_recovery, current_quality, total_done]

        def completion_rate(key, field):
            total = monthly[key]["total"]
            return round(monthly[key][field] / total * 100, 1) if total else 0

        monthly_recovery_rates = [
            completion_rate(key, "recovered") for key in month_keys
        ]
        monthly_quality_rates = [completion_rate(key, "quality") for key in month_keys]
        monthly_done_rates = [completion_rate(key, "done") for key in month_keys]

        leadtime_bucket_labels = ["0~3일", "4~7일", "8~14일", "15~30일", "31일 이상"]
        leadtime_bucket_counts = [0, 0, 0, 0, 0]
        for days in lead_exchange_return:
            if days <= 3:
                leadtime_bucket_counts[0] += 1
            elif days <= 7:
                leadtime_bucket_counts[1] += 1
            elif days <= 14:
                leadtime_bucket_counts[2] += 1
            elif days <= 30:
                leadtime_bucket_counts[3] += 1
            else:
                leadtime_bucket_counts[4] += 1

        recovery_leadtime_average = (
            round(float(np.mean(lead_exchange_return)), 1)
            if lead_exchange_return
            else None
        )
        recovery_leadtime_median = (
            round(float(np.median(lead_exchange_return)), 1)
            if lead_exchange_return
            else None
        )

        db.close()

        return templates.TemplateResponse(
            "dashboard/dashboard_page2.html",
            {
                "request": request,
                **dashboard_context,
                "page": 2,
                "product": product_name,
                "product_code": product,
                "total_exchange": current_exchange,
                "recovery_count": current_recovery,
                "waiting_count": current_waiting,
                "quality_done": current_quality,
                "total_done": total_done,
                "exchange_only_count": exchange_only_count,
                "cradle_exchange_count": cradle_exchange_count,
                "quality_pending_count": quality_pending_count,
                "analysis_pending_count": analysis_pending_count,
                "recovery_rate": recovery_rate,
                "quality_rate": quality_rate,
                "quality_overall_rate": quality_overall_rate,
                "analysis_rate": analysis_rate,
                "end_to_end_rate": end_to_end_rate,
                "long_waiting_count": long_waiting_count,
                "shortest_waiting_days": shortest_waiting_days,
                "oldest_waiting_days": oldest_waiting_days,
                "exchange_change": exchange_change,
                "exchange_diff": exchange_diff,
                "recovery_change": recovery_change,
                "recovery_diff": recovery_diff,
                "waiting_change": waiting_change,
                "waiting_diff": waiting_diff,
                "quality_change": quality_change,
                "quality_diff": quality_diff,
                "done_change": done_change,
                "done_diff": done_diff,
                "current_label": current_label,
                "compare_label": compare_label,
                "filter_start": start.strftime("%Y-%m-%d"),
                "filter_end": end.strftime("%Y-%m-%d"),
                "month_labels": month_labels,
                "monthly_exchange_chart": monthly_exchange_chart,
                "monthly_cradle_chart": monthly_cradle_chart,
                "monthly_waiting_counts": monthly_waiting_counts,
                "stage_labels": stage_labels,
                "stage_counts": stage_counts,
                "monthly_recovery_rates": monthly_recovery_rates,
                "monthly_quality_rates": monthly_quality_rates,
                "monthly_done_rates": monthly_done_rates,
                "leadtime_bucket_labels": leadtime_bucket_labels,
                "leadtime_bucket_counts": leadtime_bucket_counts,
                "waiting_age_bucket_labels": waiting_age_bucket_labels,
                "waiting_age_bucket_counts": waiting_age_bucket_counts,
                "recovery_leadtime_average": recovery_leadtime_average,
                "recovery_leadtime_median": recovery_leadtime_median,
            },
        )

    if page == 7:
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        scope = request.query_params.get("scope", "all")

        def parse_analysis_date(value, fallback):
            try:
                return datetime.strptime(value, "%Y-%m-%d") if value else fallback
            except ValueError:
                return fallback

        default_analysis_end = now if dashboard_year == now.year else dashboard_end - timedelta(days=1)
        analysis_start = parse_analysis_date(start_str, dashboard_start)
        analysis_end = parse_analysis_date(end_str, default_analysis_end)
        if analysis_start > analysis_end:
            analysis_start, analysis_end = analysis_end, analysis_start

        analysis_query = (
            db.query(CSCase, CXAnalysis, QualityInspection, LabAnalysis)
            .outerjoin(CXAnalysis, CXAnalysis.transfer_no == CSCase.transfer_no)
            .outerjoin(
                QualityInspection, QualityInspection.transfer_no == CSCase.transfer_no
            )
            .outerjoin(LabAnalysis, LabAnalysis.transfer_no == CSCase.transfer_no)
            .filter(
                CSCase.product == product,
                case_date_expr >= analysis_start,
                case_date_expr < analysis_end + timedelta(days=1),
            )
        )
        if scope == "exchange":
            analysis_query = analysis_query.filter(
                func.lower(func.trim(CSCase.exchange_type)).in_(["o", "cradle"])
            )

        analysis_rows = analysis_query.all()

        def clean_label(value):
            if value is None or not str(value).strip():
                return None
            text = str(value).strip()

            try:
                repaired = text.encode("latin1").decode("cp949")
                if repaired:
                    return repaired
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            return text

        selected_issue = request.query_params.get("issue", "")
        total_cases = len(analysis_rows)
        cx_classified = sum(
            cx is not None
            and (
                clean_label(cx.issue1) is not None or clean_label(cx.issue2) is not None
            )
            for _, cx, _, _ in analysis_rows
        )
        multi_cause_cases = sum(
            cx is not None
            and clean_label(cx.issue1) is not None
            and clean_label(cx.issue2) is not None
            for _, cx, _, _ in analysis_rows
        )
        quality_rows = sum(quality is not None for _, _, quality, _ in analysis_rows)
        quality_judged = sum(
            quality is not None
            and clean_label(quality.quality_result) in {"OK", "NG"}
            for _, _, quality, _ in analysis_rows
        )
        lab_rows = sum(lab is not None for _, _, _, lab in analysis_rows)
        lab_completed = sum(
            lab is not None and lab.analysis_completed_date is not None
            for _, _, _, lab in analysis_rows
        )

        def rate(numerator, denominator):
            return round(numerator / denominator * 100, 1) if denominator else 0

        issue_counts = defaultdict(int)
        detail_counts = defaultdict(int)
        quality_counts = defaultdict(int)
        quality_by_hw_rev = defaultdict(
            lambda: {"received": 0, "returned": 0, "inspected": 0, "defect": 0}
        )
        quality_progress_rows = []
        lab_finding_counts = defaultdict(int)

        for cs, cx, quality, lab in analysis_rows:
            hw_rev = (
                clean_label(cs.rev) or rev_from_serial(cs.serial_no or "") or "미분류"
            )
            rev_stats = quality_by_hw_rev[hw_rev]
            rev_stats["received"] += 1
            if cs.return_at is not None:
                rev_stats["returned"] += 1
            if quality is not None:
                rev_stats["inspected"] += 1
                if clean_label(quality.quality_result) == "NG":
                    rev_stats["defect"] += 1
            if cx:
                for issue in (clean_label(cx.issue1), clean_label(cx.issue2)):
                    if issue:
                        issue_counts[issue] += 1
            if quality:
                quality_counts[clean_label(quality.quality_result) or "미판정"] += 1

            if lab:
                finding = (
                    clean_label(lab.defect_type)
                    or clean_label(lab.root_cause)
                    or clean_label(lab.analysis_result)
                )
                lab_finding_counts[finding or "분류 미입력"] += 1

            if (cs.exchange_type or "").strip().lower() in {"o", "cradle"}:
                lot = (cs.lot or (cs.serial_no or "").split("-", 1)[0]).strip().upper()
                process_rev = (
                    clean_label(quality.rev) if quality and quality.rev else None
                ) or get_process_rev(lot) or "미분류"
                research_completed = bool(
                    lab
                    and (
                        clean_label(lab.root_cause)
                        or clean_label(lab.analysis_result)
                        or clean_label(lab.defect_type)
                        or clean_label(lab.remark)
                        or lab.analysis_completed_date
                    )
                )
                quality_progress_rows.append(
                    {
                        "hw_rev": hw_rev,
                        "process_rev": process_rev,
                        "case_date": cs.date_only or cs.received_at,
                        "return_at": cs.return_at,
                        "quality_date": (
                            quality.lab_transfer_date if quality else None
                        ),
                        "defect_done": bool(
                            quality
                            and clean_label(quality.quality_result) == "NG"
                        ),
                        "recovery_done": cs.return_at is not None,
                        "quality_done": bool(
                            quality
                            and clean_label(quality.quality_result) in {"OK", "NG"}
                        ),
                        "research_done": research_completed,
                        "conclusion_done": bool(
                            lab and lab.analysis_completed_date is not None
                        ),
                    }
                )

        progress_stage_labels = ["회수", "품질 검토", "연구 분석", "결론 도출"]

        def build_progress_summary(rows):
            recovery_total = len(rows)
            recovery_done = sum(row["recovery_done"] for row in rows)
            quality_total = recovery_done
            quality_done = sum(
                row["recovery_done"] and row["quality_done"] for row in rows
            )
            research_total = quality_done
            research_done = sum(
                row["quality_done"] and row["research_done"] for row in rows
            )
            conclusion_total = research_done
            conclusion_done = sum(
                row["research_done"] and row["conclusion_done"] for row in rows
            )
            pairs = [
                (recovery_total, recovery_done),
                (quality_total, quality_done),
                (research_total, research_done),
                (conclusion_total, conclusion_done),
            ]
            return [
                {
                    "label": label,
                    "total": total,
                    "completed": completed,
                    "remaining": max(total - completed, 0),
                    "rate": rate(completed, total),
                }
                for label, (total, completed) in zip(progress_stage_labels, pairs)
            ]

        def build_rev_progress(field):
            grouped = defaultdict(list)
            for row in quality_progress_rows:
                grouped[row[field] or "미분류"].append(row)
            return [
                {
                    "rev": rev,
                    "case_count": len(rows),
                    "stages": build_progress_summary(rows),
                }
                for rev, rows in sorted(grouped.items(), key=lambda item: item[0])
            ]

        def build_rev_overview(field):
            grouped = defaultdict(list)
            for row in quality_progress_rows:
                grouped[row[field] or "미분류"].append(row)
            overview = []
            for rev, rows in sorted(grouped.items(), key=lambda item: item[0]):
                received = len(rows)
                recovered = sum(row["recovery_done"] for row in rows)
                inspected = sum(row["quality_done"] for row in rows)
                defects = sum(row["defect_done"] for row in rows)
                overview.append(
                    {
                        "rev": rev,
                        "received": received,
                        "recovered": recovered,
                        "waiting_return": max(received - recovered, 0),
                        "recovery_rate": rate(recovered, received),
                        "inspected": inspected,
                        "waiting_inspection": max(recovered - inspected, 0),
                        "defects": defects,
                        "defect_rate": rate(defects, received),
                    }
                )
            return overview

        quality_progress = build_progress_summary(quality_progress_rows)
        hw_rev_progress = build_rev_progress("hw_rev")
        process_rev_progress = build_rev_progress("process_rev")
        hw_rev_overview = build_rev_overview("hw_rev")
        process_rev_overview = build_rev_overview("process_rev")

        progress_rev_mode = request.query_params.get("rev_mode", "hw")
        if progress_rev_mode not in {"hw", "process"}:
            progress_rev_mode = "hw"
        progress_rev_field = (
            "hw_rev" if progress_rev_mode == "hw" else "process_rev"
        )
        progress_rev_options = sorted(
            {row[progress_rev_field] or "미분류" for row in quality_progress_rows}
        )
        selected_progress_rev = request.query_params.get("progress_rev", "")
        if selected_progress_rev not in progress_rev_options:
            selected_progress_rev = (
                progress_rev_options[0] if progress_rev_options else ""
            )
        selected_progress_rows = [
            row
            for row in quality_progress_rows
            if (row[progress_rev_field] or "미분류") == selected_progress_rev
        ]

        weekly_keys = []
        week_cursor = analysis_start.date() - timedelta(
            days=analysis_start.date().weekday()
        )
        last_week = analysis_end.date() - timedelta(
            days=analysis_end.date().weekday()
        )
        while week_cursor <= last_week:
            weekly_keys.append((week_cursor.year, dashboard_week(week_cursor)))
            week_cursor += timedelta(days=7)
        weekly_index = {key: index for index, key in enumerate(weekly_keys)}
        received_events = [0] * len(weekly_keys)
        recovered_events = [0] * len(weekly_keys)
        defect_events = [0] * len(weekly_keys)

        def add_week_event(values, value):
            if not value:
                return
            event_date = value.date() if hasattr(value, "date") else value
            key = (event_date.year, dashboard_week(event_date))
            index = weekly_index.get(key)
            if index is not None:
                values[index] += 1

        for row in selected_progress_rows:
            add_week_event(received_events, row["case_date"])
            if row["recovery_done"]:
                add_week_event(recovered_events, row["return_at"])
            if row["defect_done"]:
                add_week_event(
                    defect_events, row["quality_date"] or row["case_date"]
                )

        cumulative_received = []
        cumulative_recovered = []
        cumulative_defects = []
        running_received = running_recovered = running_defects = 0
        for received_count, recovered_count, defect_count in zip(
            received_events, recovered_events, defect_events
        ):
            running_received += received_count
            running_recovered += recovered_count
            running_defects += defect_count
            cumulative_received.append(running_received)
            cumulative_recovered.append(running_recovered)
            cumulative_defects.append(running_defects)
        all_week_labels = [f"{year}-W{week:02d}" for year, week in weekly_keys]
        visible_week_start = max(len(weekly_keys) - 12, 0)
        weekly_progress = {
            "labels": all_week_labels[visible_week_start:],
            "received": cumulative_received[visible_week_start:],
            "recovered": cumulative_recovered[visible_week_start:],
            "recovery_rates": [
                rate(recovered, received)
                for received, recovered in zip(
                    cumulative_received, cumulative_recovered
                )
            ][visible_week_start:],
            "defects": cumulative_defects[visible_week_start:],
            "defect_rates": [
                rate(defects, received)
                for received, defects in zip(cumulative_received, cumulative_defects)
            ][visible_week_start:],
            "omitted_weeks": visible_week_start,
        }
        selected_progress_summary = build_progress_summary(selected_progress_rows)

        issue_items = sorted(
            issue_counts.items(), key=lambda item: item[1], reverse=True
        )
        issue_options = [label for label, _ in issue_items]
        if not selected_issue or selected_issue not in issue_options:
            selected_issue = issue_options[0] if issue_options else ""

        for _, cx, _, _ in analysis_rows:
            if not cx:
                continue
            cause_paths = (
                (clean_label(cx.issue1), clean_label(cx.depth1_1)),
                (clean_label(cx.issue2), clean_label(cx.depth2_1)),
            )
            for issue, detail in cause_paths:
                if issue == selected_issue:
                    detail_counts[detail or "세부 원인 미입력"] += 1

        detail_items = sorted(
            detail_counts.items(), key=lambda item: item[1], reverse=True
        )[:10]
        quality_order = ["O", "X", "미판정"]
        quality_items = [
            (label, quality_counts[label])
            for label in quality_order
            if quality_counts[label] > 0
        ]
        quality_items.extend(
            (label, count)
            for label, count in quality_counts.items()
            if label not in quality_order
        )

        def hw_rev_sort_key(item):
            label = item[0]
            if label == "미분류":
                return (2, 999, label)
            digits = "".join(character for character in label if character.isdigit())
            return (0, int(digits) if digits else 998, label)

        hw_rev_items = sorted(quality_by_hw_rev.items(), key=hw_rev_sort_key)
        hw_rev_options = [label for label, _ in hw_rev_items]
        selected_hw_rev = request.query_params.get("hw_rev", "")
        if selected_hw_rev not in hw_rev_options:
            selected_hw_rev = hw_rev_options[0] if hw_rev_options else ""
        selected_hw_rev_stats = quality_by_hw_rev.get(
            selected_hw_rev,
            {"received": 0, "returned": 0, "inspected": 0, "defect": 0},
        )
        selected_hw_rev_summary = {
            **selected_hw_rev_stats,
            "waiting_return": max(
                0,
                selected_hw_rev_stats["received"] - selected_hw_rev_stats["returned"],
            ),
            "waiting_inspection": max(
                0,
                selected_hw_rev_stats["returned"] - selected_hw_rev_stats["inspected"],
            ),
            "return_rate": rate(
                selected_hw_rev_stats["returned"],
                selected_hw_rev_stats["received"],
            ),
            "defect_rate": rate(
                selected_hw_rev_stats["defect"],
                selected_hw_rev_stats["inspected"],
            ),
        }
        lab_items = sorted(
            lab_finding_counts.items(), key=lambda item: item[1], reverse=True
        )[:10]
        top_issue_label = issue_items[0][0] if issue_items else "-"
        top_issue_count = issue_items[0][1] if issue_items else 0
        cause_occurrences = sum(issue_counts.values())

        db.close()
        return templates.TemplateResponse(
            "dashboard/dashboard_page7.html",
            {
                "request": request,
                **dashboard_context,
                "page": 7,
                "product": product_name,
                "product_code": product,
                "scope": scope,
                "filter_start": analysis_start.strftime("%Y-%m-%d"),
                "filter_end": analysis_end.strftime("%Y-%m-%d"),
                "total_cases": total_cases,
                "cx_classified": cx_classified,
                "cx_classified_rate": rate(cx_classified, total_cases),
                "cause_occurrences": cause_occurrences,
                "multi_cause_cases": multi_cause_cases,
                "multi_cause_rate": rate(multi_cause_cases, total_cases),
                "quality_rows": quality_rows,
                "quality_coverage_rate": rate(quality_rows, total_cases),
                "quality_judged": quality_judged,
                "quality_judged_rate": rate(quality_judged, quality_rows),
                "lab_rows": lab_rows,
                "lab_completed": lab_completed,
                "lab_completed_rate": rate(lab_completed, total_cases),
                "top_issue_label": top_issue_label,
                "top_issue_count": top_issue_count,
                "top_issue_rate": rate(top_issue_count, cause_occurrences),
                "issue_options": issue_options,
                "selected_issue": selected_issue,
                "issue_labels": [label for label, _ in issue_items],
                "issue_counts": [count for _, count in issue_items],
                "detail_labels": [label for label, _ in detail_items],
                "detail_counts": [count for _, count in detail_items],
                "quality_labels": [label for label, _ in quality_items],
                "quality_counts": [count for _, count in quality_items],
                "hw_rev_labels": [label for label, _ in hw_rev_items],
                "hw_rev_received": [stats["received"] for _, stats in hw_rev_items],
                "hw_rev_returned": [stats["returned"] for _, stats in hw_rev_items],
                "hw_rev_defects": [stats["defect"] for _, stats in hw_rev_items],
                "hw_rev_return_rates": [
                    rate(stats["returned"], stats["received"])
                    for _, stats in hw_rev_items
                ],
                "hw_rev_defect_rates": [
                    rate(stats["defect"], stats["inspected"])
                    for _, stats in hw_rev_items
                ],
                "hw_rev_options": hw_rev_options,
                "selected_hw_rev": selected_hw_rev,
                "selected_hw_rev_summary": selected_hw_rev_summary,
                "lab_labels": [label for label, _ in lab_items],
                "lab_counts": [count for _, count in lab_items],
                "quality_progress": quality_progress,
                "hw_rev_progress": hw_rev_progress,
                "process_rev_progress": process_rev_progress,
                "hw_rev_overview": hw_rev_overview,
                "process_rev_overview": process_rev_overview,
                "progress_rev_mode": progress_rev_mode,
                "progress_rev_options": progress_rev_options,
                "selected_progress_rev": selected_progress_rev,
                "weekly_progress": weekly_progress,
                "selected_progress_summary": selected_progress_summary,
            },
        )

    if page == 3:

        loss_reason_year = dashboard_year
        loss_reason_query = db.query(LossReason).filter(
            LossReason.product == product
        )
        if not dashboard_all_years:
            loss_reason_query = loss_reason_query.filter(
                LossReason.year == loss_reason_year
            )
        loss_reason_rows = (
            loss_reason_query
            .order_by(
                LossReason.week.desc(),
                LossReason.created_at.desc(),
                LossReason.id.desc(),
            )
            .all()
        )
        reason_by_transfer = {}
        for item in loss_reason_rows:
            if item.transfer_no and item.transfer_no not in reason_by_transfer:
                reason_by_transfer[item.transfer_no] = item

        automatic_issue_query = (
            db.query(CSCase, CXAnalysis)
            .join(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
            .filter(
                CSCase.product == product,
                CSCase.date_only.isnot(None),
                CXAnalysis.data_loss_type.in_(
                    ["general", "non_device_failure", "device_failure"]
                ),
            )
            .order_by(CSCase.date_only.desc(), CSCase.transfer_no.desc())
            .all()
        )
        automatic_issue_rows = []
        automatic_issue_counts = defaultdict(int)
        automatic_weekly_issue_counts = defaultdict(lambda: defaultdict(int))
        for case_item, analysis in automatic_issue_query:
            case_date = case_item.date_only
            if not case_date or (
                not dashboard_all_years and case_date.year != loss_reason_year
            ):
                continue
            week = dashboard_week(case_date)
            count = max(int(analysis.data_loss_count or 1), 1)
            issue_label = (
                " > ".join(
                    value
                    for value in [case_item.category_main, case_item.category_sub]
                    if value and value != "None"
                )
                or "미분류 이슈"
            )
            saved_reason = reason_by_transfer.get(case_item.transfer_no)
            chart_reason = (
                saved_reason.reason
                if saved_reason and saved_reason.reason
                else (
                    "HW불량"
                    if analysis.data_loss_type == "device_failure"
                    else "사유 미입력"
                )
            )
            automatic_issue_counts[chart_reason] += count
            automatic_weekly_issue_counts[week][chart_reason] += count
            automatic_issue_rows.append(
                {
                    "date": case_date,
                    "week": week,
                    "transfer_no": case_item.transfer_no,
                    "loss_type": analysis.data_loss_type,
                    "loss_count": count,
                    "issue": issue_label,
                    "description": case_item.description or "",
                    "result": analysis.result_summary or "",
                    "reason_id": saved_reason.id if saved_reason else None,
                    "reason": saved_reason.reason if saved_reason else "",
                    "detail": saved_reason.detail if saved_reason else "",
                }
            )

        automatic_issue_ranking = sorted(
            automatic_issue_counts.items(), key=lambda pair: pair[1], reverse=True
        )
        automatic_top_issues = [label for label, _count in automatic_issue_ranking[:8]]
        automatic_weekly_series = [
            {
                "label": f"{issue} ({automatic_issue_counts[issue]}건)",
                "data": [
                    automatic_weekly_issue_counts[int(week)].get(issue, 0)
                    for week in week_labels
                ],
            }
            for issue in automatic_top_issues
        ]
        automatic_other_weekly = [
            sum(
                count
                for issue, count in automatic_weekly_issue_counts[int(week)].items()
                if issue not in automatic_top_issues
            )
            for week in week_labels
        ]
        if any(automatic_other_weekly):
            automatic_weekly_series.append(
                {"label": "기타 이슈", "data": automatic_other_weekly}
            )
        automatic_week_summaries = []
        for week in sorted(automatic_weekly_issue_counts):
            reasons = sorted(
                automatic_weekly_issue_counts[week].items(),
                key=lambda pair: pair[1],
                reverse=True,
            )
            automatic_week_summaries.append(
                {
                    "week": week,
                    "total": sum(count for _reason, count in reasons),
                    "reasons": [
                        {"label": reason, "count": count} for reason, count in reasons
                    ],
                }
            )
        maximum_week_loss = max(
            (summary["total"] for summary in automatic_week_summaries),
            default=0,
        )
        maximum_week_loss = max(maximum_week_loss, 1)
        for summary in automatic_week_summaries:
            summary["percent"] = max(
                round(summary["total"] / maximum_week_loss * 100, 1), 4
            )
            summary["level"] = (
                "high"
                if summary["percent"] >= 75
                else "medium" if summary["percent"] >= 40 else "normal"
            )
        automatic_peak_week = max(
            automatic_week_summaries,
            key=lambda summary: summary["total"],
            default={"week": "-", "total": 0},
        )
        automatic_hw_total = sum(
            reason["count"]
            for summary in automatic_week_summaries
            for reason in summary["reasons"]
            if reason["label"] == "HW불량"
        )
        automatic_pending_total = sum(
            reason["count"]
            for summary in automatic_week_summaries
            for reason in summary["reasons"]
            if reason["label"] == "사유 미입력"
        )
        automatic_week_hw = []
        automatic_week_other = []
        automatic_week_pending = []
        for week in week_labels:
            reason_counts = automatic_weekly_issue_counts[int(week)]
            automatic_week_hw.append(reason_counts.get("HW불량", 0))
            automatic_week_pending.append(reason_counts.get("사유 미입력", 0))
            automatic_week_other.append(
                sum(
                    count
                    for reason, count in reason_counts.items()
                    if reason not in {"HW불량", "사유 미입력"}
                )
            )
        automatic_notable_reasons = [
            {
                "week": summary["week"],
                "label": reason["label"],
                "count": reason["count"],
            }
            for summary in automatic_week_summaries
            for reason in summary["reasons"]
            if reason["label"] not in {"HW불량", "사유 미입력"}
        ]
        automatic_reason_matrix = []
        for reason, total in automatic_issue_ranking:
            values = [
                automatic_weekly_issue_counts[int(week)].get(reason, 0)
                for week in week_labels
            ]
            automatic_reason_matrix.append(
                {
                    "reason": reason,
                    "total": total,
                    "values": values,
                }
            )
        automatic_week_meta = []
        previous_month = None
        for week in week_labels:
            week_number = int(week)
            week_month = datetime.fromisocalendar(
                loss_reason_year, week_number, 4
            ).month
            automatic_week_meta.append(
                {
                    "week": week_number,
                    "month": week_month,
                    "month_start": week_month != previous_month,
                    "current": (
                        loss_reason_year == now.year
                        and week_number == dashboard_week(now)
                    ),
                }
            )
            previous_month = week_month
        loss_reason_counts = defaultdict(int)
        weekly_reason_counts = defaultdict(lambda: defaultdict(int))
        for item in loss_reason_rows:
            count = max(int(item.loss_count or 1), 1)
            loss_reason_counts[item.reason] += count
            weekly_reason_counts[int(item.week)][item.reason] += count
        reason_ranking = sorted(
            loss_reason_counts.items(), key=lambda pair: pair[1], reverse=True
        )
        top_reasons = [label for label, _count in reason_ranking[:5]]
        weekly_reason_series = []
        for reason in top_reasons:
            weekly_reason_series.append(
                {
                    "label": reason,
                    "data": [
                        weekly_reason_counts[int(week)].get(reason, 0)
                        for week in week_labels
                    ],
                }
            )
        other_weekly = [
            sum(
                count
                for reason, count in weekly_reason_counts[int(week)].items()
                if reason not in top_reasons
            )
            for week in week_labels
        ]
        if any(other_weekly):
            weekly_reason_series.append({"label": "기타", "data": other_weekly})
        reason_total = sum(loss_reason_counts.values())
        running = 0
        pareto_labels = []
        pareto_counts = []
        pareto_cumulative = []
        for label, count in reason_ranking[:8]:
            running += count
            pareto_labels.append(label)
            pareto_counts.append(count)
            pareto_cumulative.append(
                round(running / reason_total * 100, 1) if reason_total else 0
            )

        db.close()

        return templates.TemplateResponse(
            "dashboard/dashboard_page3.html",
            {
                "request": request,
                **dashboard_context,
                "page": 3,
                "product": product_name,
                "product_code": product,
                "month_labels": month_labels,
                "month_counts": month_counts,
                "month_lost_include": month_lost_include,
                "month_lost_exclude": month_lost_exclude,
                "week_labels": week_labels,
                "week_counts": week_counts,
                "week_lost_include": week_lost_include,
                "week_lost_exclude": week_lost_exclude,
                "week_loss_sources": week_loss_sources,
                "week_grouped": week_grouped.to_dict(orient="records"),
                "kpi_total_count": total_count,
                "kpi_total_lost": total_lost_include,
                "kpi_rate_include": loss_rate_include,
                "kpi_rate_exclude": loss_rate_exclude,
                "kpi_target": loss_target,
                "loss_reason_year": loss_reason_year,
                "loss_reason_current_week": dashboard_week(now),
                "loss_reason_rows": loss_reason_rows[:100],
                "loss_reason_total": reason_total,
                "loss_reason_known": [label for label, _count in reason_ranking],
                "weekly_reason_series": weekly_reason_series,
                "reason_pareto_labels": pareto_labels,
                "reason_pareto_counts": pareto_counts,
                "reason_pareto_cumulative": pareto_cumulative,
                "automatic_issue_rows": automatic_issue_rows,
                "automatic_issue_total": sum(automatic_issue_counts.values()),
                "automatic_weekly_series": automatic_weekly_series,
                "automatic_week_totals": [
                    sum(automatic_weekly_issue_counts[int(week)].values())
                    for week in week_labels
                ],
                "automatic_issue_labels": [
                    label for label, _ in automatic_issue_ranking[:8]
                ],
                "automatic_issue_counts": [
                    count for _, count in automatic_issue_ranking[:8]
                ],
                "automatic_week_summaries": automatic_week_summaries,
                "automatic_peak_week": automatic_peak_week,
                "automatic_hw_total": automatic_hw_total,
                "automatic_pending_total": automatic_pending_total,
                "automatic_week_hw": automatic_week_hw,
                "automatic_week_other": automatic_week_other,
                "automatic_week_pending": automatic_week_pending,
                "automatic_notable_reasons": automatic_notable_reasons,
                "automatic_reason_matrix": automatic_reason_matrix,
                "automatic_week_meta": automatic_week_meta,
            },
        )

    if page == 4:
        all_issues = get_all_issues(
            f"project = {product_config['jira_project']} "
            "AND issuetype not in (에픽, Meeting)"
        )

        def jira_start_date(issue):
            key = issue["key"].strip().upper()
            value = (
                START_DATE_MAP[key]
                if product == "cart_bp_pro" and key in START_DATE_MAP
                else issue["fields"]["created"][:10]
            )
            return datetime.strptime(value, "%Y-%m-%d")

        if not dashboard_all_years:
            all_issues = [
                issue
                for issue in all_issues
                if jira_start_date(issue).year == dashboard_year
            ]

        def normalize_jira_status(status_name):
            normalized = (status_name or "").strip()
            compact = "".join(normalized.split()).casefold()
            if compact == "afterjob":
                return "After Job"
            if compact == "개선완료":
                return "개선 완료"
            return normalized

        if product == "cart_bp_pro":
            jira_status = {
                "개선 필요": 0,
                "개선 진행 중": 0,
                "검증 진행 중": 0,
                "배포 및 적용 예정": 0,
                "배포 및 적용 완료": 0,
                "개선 완료": 0,
                "After Job": 0,
            }

        else:
            jira_status = {
                "개선 필요": 0,
                "개선 진행 중": 0,
                "검증 진행 중": 0,
                "배포 및 적용 예정": 0,
                "배포 및 적용 완료": 0,
                "개선 완료": 0,
                "After Job": 0,
            }

        for issue in all_issues:
            status = normalize_jira_status(issue["fields"]["status"]["name"])

            if product == "cart_bp_pro":
                if status in jira_status:
                    jira_status[status] += 1

            else:
                if status in jira_status:
                    jira_status[status] += 1

        total = sum(jira_status.values())
        done = jira_status["개선 완료"]
        completion_rate = round(done / total * 100, 1) if total else 0

        now = (
            datetime.now()
            if dashboard_all_years or dashboard_year == datetime.now().year
            else dashboard_end - timedelta(seconds=1)
        )

        sla_total = 0
        sla_ok = 0
        sla_over = 0

        for issue in all_issues:

            key = issue["key"].strip().upper()
            status = normalize_jira_status(issue["fields"]["status"]["name"])

            if product == "cart_bp_pro" and key in START_DATE_MAP:
                created_dt = datetime.strptime(START_DATE_MAP[key], "%Y-%m-%d")
            else:
                created_dt = datetime.strptime(
                    issue["fields"]["created"][:10], "%Y-%m-%d"
                )

            if status == "개선 완료":
                done_dt = datetime.strptime(get_done_date(issue, product), "%Y-%m-%d")
                diff_days = (done_dt - created_dt).days
            else:
                diff_days = (now - created_dt).days

            if diff_days < 0:
                continue

            sla_total += 1

            if diff_days <= 120:
                sla_ok += 1
            else:
                sla_over += 1

        sla_rate = round((sla_ok / sla_total) * 100, 1) if sla_total else 0

        weekly_created = defaultdict(int)
        weekly_resolved = defaultdict(int)

        for issue in all_issues:

            created = issue["fields"]["created"][:10]
            created_dt = datetime.strptime(created, "%Y-%m-%d")
            week_key = f"{created_dt.year}-W{dashboard_week(created_dt)}"

            weekly_created[week_key] += 1

            if normalize_jira_status(issue["fields"]["status"]["name"]) == "개선 완료":

                done_date = get_done_date(issue, product)

                if not done_date:
                    done_date = issue["fields"]["updated"][:10]

                done_dt = datetime.strptime(done_date, "%Y-%m-%d")

                week_key = f"{done_dt.year}-W{dashboard_week(done_dt)}"
                weekly_resolved[week_key] += 1

        all_weeks = sorted(
            set(weekly_created.keys()) | set(weekly_resolved.keys()),
            key=lambda x: (int(x.split("-W")[0]), int(x.split("-W")[1])),
        )

        trend_labels = []
        trend_created = []
        trend_resolved = []

        c_sum = 0
        r_sum = 0

        for w in all_weeks:
            c_sum += weekly_created[w]
            r_sum += weekly_resolved[w]

            trend_labels.append(w)
            trend_created.append(c_sum)
            trend_resolved.append(r_sum)

        issue_table = []

        def current_status_since(issue, status_name):
            transition_dates = []
            for history in issue.get("changelog", {}).get("histories", []):
                if any(
                    item.get("field") == "status"
                    and normalize_jira_status(item.get("toString")) == status_name
                    for item in history.get("items", [])
                ):
                    try:
                        transition_dates.append(
                            datetime.fromisoformat(
                                history["created"].replace("Z", "+00:00")
                            ).replace(tzinfo=None)
                        )
                    except (KeyError, TypeError, ValueError):
                        pass
            if transition_dates:
                return max(transition_dates)
            try:
                return datetime.fromisoformat(
                    issue["fields"]["created"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (KeyError, TypeError, ValueError):
                return now

        issue_keys = [issue["key"].strip().upper() for issue in all_issues]
        notes = (
            db.query(JiraIssueNote).filter(JiraIssueNote.jira_key.in_(issue_keys)).all()
            if issue_keys
            else []
        )
        note_map = {note.jira_key: note.description for note in notes}

        for issue in all_issues:

            fields = issue["fields"]

            key = issue["key"].strip().upper()
            status = normalize_jira_status(fields["status"]["name"])
            summary = fields["summary"]
            priority = (fields.get("priority") or {}).get("name") or "미지정"
            assignee = (fields.get("assignee") or {}).get("displayName") or "미지정"
            # Jira에서는 Component가 아니라 label을 운영 팀 기준으로 사용한다.
            team_names = [
                str(label).strip()
                for label in (fields.get("labels") or [])
                if str(label).strip()
            ] or ["미지정"]
            fix_versions = [
                version.get("name", "").strip()
                for version in (fields.get("fixVersions") or [])
                if version.get("name", "").strip()
            ] or ["미지정"]
            issue_type = (fields.get("issuetype") or {}).get("name") or "미지정"

            if product == "cart_bp_pro" and key in START_DATE_MAP:
                improve_date = START_DATE_MAP[key]
            else:
                improve_date = fields["created"][:10]

            key = issue["key"].strip().upper()

            complete_date = ""
            if status == "개선 완료":
                complete_date = get_done_date(issue, product)

            deploy_date = ""

            if status in ["배포 및 적용 완료", "개선 완료"]:

                if product == "cart_bp_pro" and key in DONE_DATE_MAP and complete_date:
                    complete_dt = datetime.strptime(complete_date, "%Y-%m-%d")
                    deploy_date = (complete_dt - timedelta(days=30)).strftime(
                        "%Y-%m-%d"
                    )

                else:
                    deploy_date = get_deploy_date(issue)

            improve_dt = datetime.strptime(improve_date, "%Y-%m-%d")

            if complete_date:
                duration = (
                    datetime.strptime(complete_date, "%Y-%m-%d") - improve_dt
                ).days
            else:
                duration = (now - improve_dt).days

            stage_started_at = current_status_since(issue, status)
            stage_days = max(0, (now - stage_started_at).days)
            try:
                updated_dt = datetime.fromisoformat(
                    fields["updated"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (KeyError, TypeError, ValueError):
                updated_dt = improve_dt
            stale_days = max(0, (now - updated_dt).days)

            observe_days = ""

            if status in ["배포 및 적용 완료", "개선 완료"] and deploy_date:

                deploy_dt = datetime.strptime(deploy_date, "%Y-%m-%d")

                if complete_date:
                    observe_days = (
                        datetime.strptime(complete_date, "%Y-%m-%d") - deploy_dt
                    ).days

                else:
                    observe_days = (now - deploy_dt).days

            issue_table.append(
                {
                    "key": key,
                    "summary": summary,
                    "status": status,
                    "improve_date": improve_date,
                    "deploy_date": deploy_date,
                    "observe_days": observe_days,
                    "complete_date": complete_date,
                    "duration": duration,
                    "description": note_map.get(key, ""),
                    "priority": priority,
                    "assignee": assignee,
                    "teams": team_names,
                    "fix_versions": fix_versions,
                    "issue_type": issue_type,
                    "due_date": fields.get("duedate") or "",
                    "updated_date": updated_dt.strftime("%Y-%m-%d"),
                    "stale_days": stale_days,
                    "stage_days": stage_days,
                }
            )

        completed_statuses = {"개선 완료"}
        active_issues = [
            issue for issue in issue_table if issue["status"] not in completed_statuses
        ]
        residual_issues = sorted(
            active_issues,
            key=lambda issue: (
                (
                    list(jira_status).index(issue["status"])
                    if issue["status"] in jira_status
                    else 999
                ),
                -issue["duration"],
                issue["key"],
            ),
        )
        residual_groups = []
        residual_status_order = list(jira_status) + [
            issue["status"]
            for issue in residual_issues
            if issue["status"] not in jira_status
        ]
        for status_name in dict.fromkeys(residual_status_order):
            if status_name in completed_statuses:
                continue
            grouped = [
                issue for issue in residual_issues if issue["status"] == status_name
            ]
            if grouped:
                average_stage_days = round(
                    sum(issue["stage_days"] for issue in grouped) / len(grouped)
                )
                maximum_stage_days = max(issue["stage_days"] for issue in grouped)
                residual_groups.append(
                    {
                        "status": status_name,
                        "average_stage_days": average_stage_days,
                        "maximum_stage_days": maximum_stage_days,
                        "issues": grouped,
                    }
                )
        jira_control = {
            "residual_groups": residual_groups,
            "active": len(active_issues),
            "over_sla": sum(issue["duration"] > 120 for issue in active_issues),
            "over_90": sum(issue["duration"] > 90 for issue in active_issues),
            "waiting": sum(
                issue["status"] in {"검증 진행 중", "배포 및 적용 예정"}
                for issue in active_issues
            ),
        }

        recent_week_keys = []
        current_monday = now.date() - timedelta(days=now.weekday())
        for offset in range(11, -1, -1):
            week_date = current_monday - timedelta(weeks=offset)
            recent_week_keys.append(
                f"{week_date.year}-W{dashboard_week(week_date)}"
            )

        aging_ranges = [
            ("0~30일", 0, 30),
            ("31~60일", 31, 60),
            ("61~90일", 61, 90),
            ("91~120일", 91, 120),
            ("120일+", 121, None),
        ]
        priority_counts = defaultdict(int)
        release_counts = defaultdict(int)
        team_groups = defaultdict(list)
        status_groups = defaultdict(list)
        for issue in active_issues:
            priority_counts[issue["priority"]] += 1
            for version in issue["fix_versions"]:
                release_counts[version] += 1
            for team_name in issue["teams"]:
                team_groups[team_name].append(issue)
            status_groups[issue["status"]].append(issue)

        def bottleneck_rows(groups, name_key):
            rows = []
            for name, issues in groups.items():
                count = len(issues)
                rows.append(
                    {
                        name_key: name,
                        "count": count,
                        "avg_age": round(sum(x["duration"] for x in issues) / count),
                        "avg_stage": round(
                            sum(x["stage_days"] for x in issues) / count
                        ),
                        "over_sla": sum(x["duration"] > 120 for x in issues),
                    }
                )
            return sorted(
                rows,
                key=lambda row: (row["over_sla"], row["avg_stage"], row["count"]),
                reverse=True,
            )

        jira_insights = {
            "week_labels": recent_week_keys,
            "week_created": [weekly_created[key] for key in recent_week_keys],
            "week_resolved": [weekly_resolved[key] for key in recent_week_keys],
            "week_net": [
                weekly_created[key] - weekly_resolved[key] for key in recent_week_keys
            ],
            "aging_labels": [label for label, _minimum, _maximum in aging_ranges],
            "aging_counts": [
                sum(
                    issue["duration"] >= minimum
                    and (maximum is None or issue["duration"] <= maximum)
                    for issue in active_issues
                )
                for _label, minimum, maximum in aging_ranges
            ],
            "priorities": sorted(
                priority_counts.items(), key=lambda item: item[1], reverse=True
            ),
            "releases": sorted(
                release_counts.items(), key=lambda item: item[1], reverse=True
            )[:8],
            "teams": bottleneck_rows(team_groups, "team")[:8],
            "statuses": bottleneck_rows(status_groups, "status"),
            "stale": sorted(
                (issue for issue in active_issues if issue["stale_days"] >= 14),
                key=lambda issue: (issue["stale_days"], issue["duration"]),
                reverse=True,
            )[:10],
        }
        jira_insights["priority_labels"] = [
            label for label, _count in jira_insights["priorities"]
        ]
        jira_insights["priority_counts"] = [
            count for _label, count in jira_insights["priorities"]
        ]
        jira_insights["release_labels"] = [
            label for label, _count in jira_insights["releases"]
        ]
        jira_insights["release_counts"] = [
            count for _label, count in jira_insights["releases"]
        ]

        db.close()

        return templates.TemplateResponse(
            "dashboard/dashboard_page4.html",
            {
                "request": request,
                **dashboard_context,
                "page": 4,
                "product": product_name,
                "product_code": product,
                "jira_status": jira_status,
                "kpi_total": total,
                "kpi_done": done,
                "kpi_rate": completion_rate,
                "trend_labels": trend_labels,
                "trend_created": trend_created,
                "trend_resolved": trend_resolved,
                "sla_total": sla_total,
                "sla_ok": sla_ok,
                "sla_over": sla_over,
                "sla_rate": sla_rate,
                "issue_table": issue_table,
                "jira_control": jira_control,
                "jira_insights": jira_insights,
                "report_date": now.strftime("%Y-%m-%d"),
            },
        )

    if page == 5:
        lot_prefix = f"F{dashboard_year % 100:02d}"
        lot_stats = [
            lot
            for lot in get_lot_stats(db)
            if dashboard_all_years or lot["lot"].upper().startswith(lot_prefix)
        ]

        total_quantity = sum([lot["quantity"] for lot in lot_stats])

        filtered_lots = [
            lot
            for lot in lot_stats
            if lot["quantity"] >= 20
        ]

        top_lots = sorted(filtered_lots, key=lambda x: x["defect_rate"], reverse=True)[
            :5
        ]

        grouped_chart_lots = defaultdict(list)

        for lot in filtered_lots:
            year_key = lot["lot"][:3]
            grouped_chart_lots[year_key].append(lot)

        grouped_chart_lots = dict(sorted(grouped_chart_lots.items()))

        db.close()
        return templates.TemplateResponse(
            "dashboard/dashboard_page5.html",
            {
                "request": request,
                **dashboard_context,
                "page": 5,
                "product": product_name,
                "product_code": product,
                "lots": lot_stats,
                "top_lots": top_lots,
                "grouped_chart_lots": grouped_chart_lots,
                "total_quantity": total_quantity,
            },
        )


@router.post("/dashboard/jira-note")
def save_jira_note(
    jira_key: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):

    note = db.query(JiraIssueNote).filter(JiraIssueNote.jira_key == jira_key).first()

    if note:
        note.description = description

    else:
        note = JiraIssueNote(jira_key=jira_key, description=description)
        db.add(note)

    db.commit()

    return JSONResponse({"success": True})