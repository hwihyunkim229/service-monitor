from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from app.database import SessionLocal
from app.models.user import User
from sqlalchemy.orm import Session
from app.security import pwd_context

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login")
def login_page(request: Request):
    success = request.session.pop("success", None)
    return templates.TemplateResponse(
        "login.html", {"request": request, "success": success, "version": "20260309"}
    )


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    db: Session = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    if not user or not pwd_context.verify(password, user.password):
        db.close()
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "아이디 또는 비밀번호가 올바르지 않습니다."},
        )

    request.session["user"] = user.username
    request.session["user_id"] = user.id
    request.session["team"] = user.team
    request.session["role"] = user.role
    request.session["force_password_change"] = bool(user.force_password_change)
    must_change = bool(user.force_password_change)
    db.close()
    response = RedirectResponse(
        url="/change-password" if must_change else "/cart_bp_pro/cx", status_code=302
    )
    return response


@router.get("/change-password")
def change_password_page(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("change_password.html", {"request": request})


@router.post("/change-password")
def change_password(
    request: Request, password: str = Form(...), password_confirm: str = Form(...)
):
    if password != password_confirm or len(password) < 8:
        return templates.TemplateResponse(
            "change_password.html",
            {
                "request": request,
                "error": "비밀번호는 8자 이상이며 두 입력값이 같아야 합니다.",
            },
        )
    db = SessionLocal()
    user = db.query(User).filter(User.id == request.session.get("user_id")).first()
    if not user:
        db.close()
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    user.password = pwd_context.hash(password)
    user.force_password_change = False
    db.commit()
    db.close()
    request.session.clear()
    request.session["success"] = (
        "비밀번호가 변경되었습니다. 새 비밀번호로 로그인하세요."
    )
    return RedirectResponse("/login", status_code=303)


@router.get("/logout")
def logout(request: Request):

    request.session.clear()
    request.session["success"] = "안전하게 로그아웃되었습니다."

    return RedirectResponse(url="/login", status_code=302)