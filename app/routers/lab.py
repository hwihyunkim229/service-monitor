from fastapi import APIRouter, Request, Body, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import String, asc, desc, case, cast, or_
from app.database import SessionLocal
from app.models.cs import CSCase
from app.models.quality import QualityInspection
from app.models.lab import LabAnalysis
from app.models.cx import CXAnalysis
from app.services.quality_schema import QUALITY_COLUMNS, QUALITY_LABELS
from app.models.rnd_transfer import (
    RndTransferHistory,
    RndTransferItem,
    RndTransferRequest,
)
from app.utils.query_filters import apply_column_filters
from fastapi import HTTPException
import os
import json
import shutil

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def safe_json(v):
    if not v:
        return []

    try:
        return json.loads(v)
    except:
        return [v]


templates.env.filters["from_json"] = safe_json

UPLOAD_DIR = os.path.join(os.getcwd(), "uploads", "rnd", "product")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.get("/{product}/lab")
def lab_list(request: Request, product: str):

    db: Session = SessionLocal()
    keyword = request.query_params.get("keyword", "").strip()
    order = request.query_params.get("order", "desc")
    order = order if order in {"asc", "desc"} else "desc"

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        requested_limit = int(request.query_params.get("limit", 50))
    except (TypeError, ValueError):
        requested_limit = 50
    limit = requested_limit if requested_limit in {20, 50, 100} else 50

    offset = (page - 1) * limit

    query = (
        db.query(CSCase, QualityInspection, LabAnalysis, CXAnalysis)
        .join(QualityInspection, CSCase.transfer_no == QualityInspection.transfer_no)
        .join(LabAnalysis, CSCase.transfer_no == LabAnalysis.transfer_no)
        .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
        .filter(
            CSCase.product == product,
        )
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
            QualityInspection.lab_transfer_date,
            *LabAnalysis.__table__.columns,
        ]
        query = query.filter(
            or_(*[cast(column, String).ilike(token) for column in search_columns])
        )
    query = apply_column_filters(
        query,
        request.query_params,
        {
            "식별코드": CSCase.transfer_no,
            "S/N": [LabAnalysis.serial_no, CSCase.serial_no],
            "품질 분석 완료일": QualityInspection.lab_transfer_date,
            "불량 원인": LabAnalysis.root_cause,
            "분석 결과": LabAnalysis.analysis_result,
            "불량 유형": LabAnalysis.defect_type,
            "비고": LabAnalysis.remark,
            "분석 완료일": LabAnalysis.analysis_completed_date,
            "첨부 자료": LabAnalysis.attachment,
        },
    )

    total_count = query.count()

    total_pages = (total_count + limit - 1) // limit

    order_fn = asc if order == "asc" else desc
    rows = (
        query.order_by(
            QualityInspection.lab_transfer_date.is_(None),
            order_fn(QualityInspection.lab_transfer_date),
            order_fn(CSCase.id),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    rows_with_lab = []

    for cs, quality, lab, cx in rows:
        rows_with_lab.append({"cs": cs, "quality": quality, "lab": lab, "cx": cx})

    response = templates.TemplateResponse(
        "lab_list.html",
        {
            "request": request,
            "rows": rows_with_lab,
            "product": product,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "keyword": keyword,
            "order": order,
            "quality_columns": QUALITY_COLUMNS,
            "quality_labels": QUALITY_LABELS,
        },
    )

    db.close()

    return response


@router.get("/{product}/lab/transfer-requests")
def list_rnd_transfer_requests(product: str, status: str = None):
    db = SessionLocal()
    try:
        query = db.query(RndTransferRequest).filter(
            RndTransferRequest.product == product
        )
        if status:
            query = query.filter(RndTransferRequest.status == status)
        requests = query.order_by(RndTransferRequest.requested_at.desc()).all()
        return {
            "requests": [
                {
                    "id": item.id,
                    "request_type": item.request_type or "set",
                    "urgent": bool(item.urgent),
                    "status": item.status,
                    "rejection_reason": item.rejection_reason,
                    "requested_at": (
                        f"{item.requested_at.isoformat()}Z" if item.requested_at else None
                    ),
                    "items": [
                        {"transfer_no": row.transfer_no, "serial_no": row.serial_no}
                        for row in item.items
                    ],
                }
                for item in requests
            ]
        }
    finally:
        db.close()


@router.post("/{product}/lab/transfer-requests/{request_id}/review")
def review_rnd_transfer_request(product: str, request_id: int, data: dict = Body(...)):
    decision = (data.get("decision") or "").strip().lower()
    reason = (data.get("reason") or "").strip()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="승인 또는 반려를 선택하세요.")
    if decision == "rejected" and not reason:
        raise HTTPException(status_code=400, detail="반려 사유를 입력하세요.")

    db = SessionLocal()
    try:
        transfer_request = (
            db.query(RndTransferRequest)
            .filter(
                RndTransferRequest.id == request_id,
                RndTransferRequest.product == product,
            )
            .first()
        )
        if not transfer_request:
            raise HTTPException(status_code=404, detail="전달 요청을 찾을 수 없습니다.")
        if transfer_request.status != "requested":
            raise HTTPException(status_code=409, detail="이미 처리된 전달 요청입니다.")

        if decision == "approved":
            for item in transfer_request.items:
                quality = (
                    db.query(QualityInspection)
                    .filter(QualityInspection.transfer_no == item.transfer_no)
                    .first()
                )
                if not quality:
                    raise HTTPException(
                        status_code=400,
                        detail=f"품질 데이터가 없습니다: {item.serial_no}",
                    )
                lab = (
                    db.query(LabAnalysis)
                    .filter(LabAnalysis.transfer_no == item.transfer_no)
                    .first()
                )
                if not lab:
                    db.add(
                        LabAnalysis(
                            transfer_no=item.transfer_no, serial_no=item.serial_no
                        )
                    )

        transfer_request.status = decision
        transfer_request.reviewed_at = datetime.utcnow()
        transfer_request.rejection_reason = reason if decision == "rejected" else None
        db.add(
            RndTransferHistory(
                request_id=request_id, status=decision, reason=reason or None
            )
        )
        db.commit()
        return {"status": decision}
    finally:
        db.close()


@router.post("/{product}/lab/update/{lab_id}")
def update_lab_cell(product: str, lab_id: int, data: dict = Body(...)):

    db = SessionLocal()

    field = data.get("field")
    value = data.get("value")

    lab = db.query(LabAnalysis).filter(LabAnalysis.id == lab_id).first()

    if not lab:
        db.close()
        return {"status": "error"}

    if field == "analysis_completed_date" and value:
        value = datetime.strptime(value, "%Y-%m-%d").date()

    setattr(lab, field, value)

    db.commit()
    db.close()

    return {"status": "success"}


@router.post("/{product}/lab/upload/{transfer_no}")
def upload_file(transfer_no: str, product: str, file: UploadFile = File(...)):

    db = SessionLocal()

    lab = db.query(LabAnalysis).filter(LabAnalysis.transfer_no == transfer_no).first()

    if not lab:

        lab = LabAnalysis(transfer_no=transfer_no)

        db.add(lab)
        db.commit()
        db.refresh(lab)

    original_name = os.path.basename(file.filename or "attachment")
    filename = f"{transfer_no}_{original_name}"

    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    files = safe_json(lab.attachment)

    files.append(filename)

    lab.attachment = json.dumps(files)

    db.commit()
    db.close()

    return {"filename": filename}


@router.get("/{product}/lab/file")
def download_file(product: str, filename: str):
    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename:
        raise HTTPException(status_code=400, detail="올바르지 않은 파일명입니다.")

    filepath = os.path.abspath(os.path.join(UPLOAD_DIR, safe_name))
    upload_root = os.path.abspath(UPLOAD_DIR)
    if os.path.commonpath([filepath, upload_root]) != upload_root:
        raise HTTPException(status_code=400, detail="올바르지 않은 파일 경로입니다.")
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")

    db = SessionLocal()
    try:
        product_labs = (
            db.query(LabAnalysis)
            .join(CSCase, CSCase.transfer_no == LabAnalysis.transfer_no)
            .filter(CSCase.product == product)
            .all()
        )
        if not any(safe_name in safe_json(lab.attachment) for lab in product_labs):
            raise HTTPException(status_code=404, detail="이 제품의 첨부파일이 아닙니다.")
    finally:
        db.close()

    return FileResponse(filepath, filename=safe_name)


@router.post("/{product}/lab/delete-file")
def delete_file(product: str, data: dict = Body(...)):

    db = SessionLocal()

    filename = data.get("filename")

    filepath = os.path.join(UPLOAD_DIR, filename)

    if os.path.exists(filepath):
        os.remove(filepath)

    labs = db.query(LabAnalysis).all()

    for lab in labs:

        files = safe_json(lab.attachment)

        if filename in files:

            files.remove(filename)

            lab.attachment = json.dumps(files)

            break

    db.commit()
    db.close()

    return {"status": "ok"}