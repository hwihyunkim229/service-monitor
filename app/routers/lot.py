from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse
from app.database import SessionLocal
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.utils.csv_parser import parse_lot_csv
from app.services.lot_service import upload_lot_data, get_all_lots

router = APIRouter(prefix="/lot", tags=["LOT"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse("lot_upload.html", {"request": request})


@router.post("/upload")
async def upload_lot(
    request: Request,
    product: str = Form("cart_bp_pro"),
    file: UploadFile = File(...),
):
    db = SessionLocal()
    data = parse_lot_csv(file)
    upload_lot_data(db, data, week=None)

    db.close()

    return RedirectResponse(url=f"/{product}/dashboard?page=5", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
def lot_dashboard(request: Request):

    db = SessionLocal()

    lots = get_all_lots(db)

    db.close()

    return templates.TemplateResponse(
        "dashboard/dashboard_page5.html", {"request": request, "lots": lots}
    )