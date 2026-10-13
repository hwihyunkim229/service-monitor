from fastapi import APIRouter, Request, Form, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import selectinload, Session
from starlette.status import HTTP_303_SEE_OTHER
from sqlalchemy import asc, desc
from io import BytesIO
import pandas as pd
from fastapi import UploadFile, File
import csv
from io import StringIO
from app.utils.tabular_upload import read_rows
from app.database import SessionLocal
from app.models.cs import CSCase
from app.models.cx import CXAnalysis
from app.utils.downloads import dated_excel_disposition
from sqlalchemy.orm import contains_eager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/{product}/cx")
def cx_list(request: Request, product: str):

    db: Session = SessionLocal()

    def clean(v):
        return v.strip() if v else ""

    filters = {
        "transfer_no": clean(request.query_params.get("transfer_no")),
        "category_main": clean(request.query_params.get("category_main")),
        "category_sub": clean(request.query_params.get("category_sub")),
        "serial_no": clean(request.query_params.get("serial_no")),
        "issue1": clean(request.query_params.get("issue1")),
        "depth1_1": clean(request.query_params.get("depth1_1")),
        "depth1_2": clean(request.query_params.get("depth1_2")),
        "issue2": clean(request.query_params.get("issue2")),
        "depth2_1": clean(request.query_params.get("depth2_1")),
        "depth2_2": clean(request.query_params.get("depth2_2")),
        "result_summary": clean(request.query_params.get("result_summary")),
    }

    query = (
        db.query(CSCase)
        .options(selectinload(CSCase.cx))
        .filter(CSCase.product == product)
    )

    category_main_options = [
        value
        for (value,) in (
            db.query(CSCase.category_main)
            .filter(
                CSCase.product == product,
                CSCase.category_main.isnot(None),
                CSCase.category_main != "",
            )
            .distinct()
            .order_by(CSCase.category_main)
            .all()
        )
    ]
    category_sub_options = [
        value
        for (value,) in (
            db.query(CSCase.category_sub)
            .filter(
                CSCase.product == product,
                CSCase.category_sub.isnot(None),
                CSCase.category_sub != "",
            )
            .distinct()
            .order_by(CSCase.category_sub)
            .all()
        )
    ]

    if filters["transfer_no"]:
        query = query.filter(CSCase.transfer_no.contains(filters["transfer_no"]))

    if filters["category_main"]:
        query = query.filter(CSCase.category_main == filters["category_main"])

    if filters["category_sub"]:
        query = query.filter(CSCase.category_sub == filters["category_sub"])

    if filters["serial_no"]:
        query = query.filter(CSCase.serial_no.contains(filters["serial_no"]))

    if filters["issue1"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.issue1 == filters["issue1"]))

    if filters["depth1_1"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.depth1_1 == filters["depth1_1"]))

    if filters["depth1_2"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.depth1_2 == filters["depth1_2"]))

    if filters["issue2"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.issue2 == filters["issue2"]))

    if filters["depth2_1"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.depth2_1 == filters["depth2_1"]))

    if filters["depth2_2"]:
        query = query.filter(CSCase.cx.has(CXAnalysis.depth2_2 == filters["depth2_2"]))

    if filters["result_summary"]:
        query = query.filter(
            CSCase.cx.has(CXAnalysis.result_summary.contains(filters["result_summary"]))
        )

    page = int(request.query_params.get("page", 1))

    limit = 100

    offset = (page - 1) * limit

    total_count = query.count()

    total_pages = (total_count + limit - 1) // limit

    cs_list = query.order_by(CSCase.id.asc()).offset(offset).limit(limit).all()

    response = templates.TemplateResponse(
        "cx_list.html",
        {
            "request": request,
            "cs_list": cs_list,
            "product": product,
            "filters": filters,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_count": total_count,
            "category_main_options": category_main_options,
            "category_sub_options": category_sub_options,
        },
    )

    db.close()

    return response


@router.post("/{product}/cx/upload_csv")
async def upload_cx_csv(product: str, file: UploadFile = File(...)):

    contents = await file.read()

    reader = read_rows(contents, file.filename)

    db = SessionLocal()

    def clean(v):
        if v is None:
            return None

        v = str(v).strip()
        if v.lower() == "none":
            return None

        return str(v).replace("\r", "").replace("\t", "").lstrip("\n").strip()

    def get_val(row, idx):
        return row[idx] if len(row) > idx else None

    cs_list = (
        db.query(CSCase)
        .filter(CSCase.product == product)
        .order_by(CSCase.id.asc())
        .all()
    )

    csv_map = {}

    for row in reader:
        if not row:
            continue

        transfer_no = clean(get_val(row, 0))

        if not transfer_no:
            continue

        if not any(str(cell).strip() for cell in row):
            continue

        csv_map[transfer_no] = row

    print(f"📊 CSV rows: {len(csv_map)}, CS rows: {len(cs_list)}")

    updated_count = 0

    for cs in cs_list:

        row = csv_map.get(cs.transfer_no)

        if not row:
            continue

        try:
            while len(row) < 8:
                row.append(None)

            cx = (
                db.query(CXAnalysis)
                .filter(CXAnalysis.transfer_no == cs.transfer_no)
                .first()
            )

            if not cx:
                cx = CXAnalysis(transfer_no=cs.transfer_no)
                db.add(cx)
                db.flush()

            cx.issue1 = clean(get_val(row, 1))
            cx.depth1_1 = clean(get_val(row, 2))
            cx.depth1_2 = clean(get_val(row, 3))
            cx.issue2 = clean(get_val(row, 4))
            cx.depth2_1 = clean(get_val(row, 5))
            cx.depth2_2 = clean(get_val(row, 6))
            cx.result_summary = clean(get_val(row, 7))
            updated_count += 1
        except Exception as e:
            print(f"❌ CSV ERROR:", row)
            print("👉", e)
            continue

    db.commit()
    db.close()

    print(f"✅ 업데이트 완료: {updated_count}건")

    return RedirectResponse(url=f"/{product}/cx", status_code=303)


@router.get("/{product}/cx/export")
def export_cx(product: str):

    db = SessionLocal()
    rows = (
        db.query(CSCase)
        .outerjoin(CXAnalysis, CSCase.transfer_no == CXAnalysis.transfer_no)
        .filter(CSCase.product == product)
        .all()
    )
    data = []
    for cs in rows:
        cx = cs.cx
        data.append(
            {
                "식별코드": cs.transfer_no,
                "일자": cs.received_at,
                "유형(대분류)": cs.category_main,
                "유형(소분류)": cs.category_sub,
                "Serial No": cs.serial_no,
                "LOT": "" if cs.lot in (None, "None") else cs.lot,
                "Size": cs.size,
                "불량 원인1": (cx.issue1 or "") if cx else "",
                "Depth1-1": (cx.depth1_1 or "") if cx else "",
                "Depth1-2": (cx.depth1_2 or "") if cx else "",
                "불량 원인2": (cx.issue2 or "") if cx else "",
                "Depth2-1": (cx.depth2_1 or "") if cx else "",
                "Depth2-2": (cx.depth2_2 or "") if cx else "",
                "처리 결과": (cx.result_summary or "") if cx else "",
            }
        )
    df = pd.DataFrame(data)

    stream = BytesIO()
    df.to_excel(stream, index=False)
    stream.seek(0)

    db.close()

    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": dated_excel_disposition(product, "CX 분석")},
    )


@router.get("/{product}/cx/{transfer_no}")
def cx_detail(request: Request, product: str, transfer_no: str):

    db: Session = SessionLocal()

    cs = (
        db.query(CSCase)
        .filter(CSCase.transfer_no == transfer_no, CSCase.product == product)
        .first()
    )

    cx = db.query(CXAnalysis).filter(CXAnalysis.transfer_no == transfer_no).first()

    db.close()

    return templates.TemplateResponse(
        "cx_detail.html", {"request": request, "cs": cs, "cx": cx, "product": product}
    )


@router.post("/{product}/cx/{transfer_no}")
def cx_save(
    product: str,
    transfer_no: str,
    result_summary: str = Form(),
    issue1: str = Form(),
    depth1_1: str = Form(),
    depth1_2: str = Form(),
    issue2: str = Form(),
    depth2_1: str = Form(),
    depth2_2: str = Form(),
):
    db = SessionLocal()

    cx = db.query(CXAnalysis).filter(CXAnalysis.transfer_no == transfer_no).first()

    if cx:
        cx.issue1 = issue1
        cx.depth1_1 = depth1_1
        cx.depth1_2 = depth1_2
        cx.issue2 = issue2
        cx.depth2_1 = depth2_1
        cx.depth2_2 = depth2_2
        cx.result_summary = result_summary
    else:
        cx = CXAnalysis(
            transfer_no=transfer_no,
            result_summary=result_summary,
            issue1=issue1,
            depth1_1=depth1_1,
            depth1_2=depth1_2,
            issue2=issue2,
            depth2_1=depth2_1,
            depth2_2=depth2_2,
        )
        db.add(cx)

    db.commit()
    db.close()

    return RedirectResponse(url=f"/{product}/cx", status_code=HTTP_303_SEE_OTHER)


@router.post("/{product}/cx/update/{cs_id}")
def update_cx(product: str, cs_id: int, data: dict = Body(...)):

    db = SessionLocal()

    cs = db.query(CSCase).filter(CSCase.id == cs_id, CSCase.product == product).first()

    if not cs:
        db.close()
        return {"status": "not found"}

    if not cs.cx:
        cx = CXAnalysis(transfer_no=cs.transfer_no)
        db.add(cx)
        db.flush()
        cs.cx = cx

    field = data.get("field")
    value = data.get("value")

    if hasattr(cs.cx, field):
        setattr(cs.cx, field, value)

    print("UPDATE TARGET:", cs.transfer_no)
    print("FIELD:", field)
    print("VALUE:", value)

    all_rows = (
        db.query(CXAnalysis).filter(CXAnalysis.transfer_no == cs.transfer_no).all()
    )

    print("CX ROW COUNT:", len(all_rows))

    db.commit()
    db.close()

    return {"status": "ok"}