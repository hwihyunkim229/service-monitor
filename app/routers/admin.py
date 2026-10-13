import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.database import SessionLocal
from app.models.user import User, UserPermission
from app.models.product import OperatingProduct
from app.security import ROLE_LABELS, TEAM_LABELS, pwd_context
from app.services.products import list_products
from app.services.permissions import ACTIONS, SECTIONS, default_permissions

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def admin_context(request, **extra):
    db = SessionLocal()
    users = db.query(User).order_by(User.id.asc()).all()
    db.close()
    return {
        "request": request,
        "users": users,
        "teams": TEAM_LABELS,
        "roles": ROLE_LABELS,
        "products": list_products(),
        **extra,
    }


@router.get("/admin")
def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", admin_context(request))


@router.post("/admin/users")
def create_user(
    request: Request,
    username: str = Form(...),
    initial_password: str = Form(...),
    team: str = Form(...),
    role: str = Form(...),
):
    username = username.strip()
    if (
        not username
        or len(initial_password) < 8
        or team not in TEAM_LABELS
        or role not in ROLE_LABELS
    ):
        return templates.TemplateResponse(
            "admin.html",
            admin_context(
                request, error="ID, 8자 이상 초기 비밀번호, 팀, 권한을 확인하세요."
            ),
        )
    db = SessionLocal()
    exists = db.query(User).filter(User.username == username).first()
    if exists:
        db.close()
        return templates.TemplateResponse(
            "admin.html", admin_context(request, error="이미 존재하는 ID입니다.")
        )
    user = User(
            username=username,
            password=pwd_context.hash(initial_password),
            team=team,
            role=role,
            force_password_change=True,
        )
    db.add(user)
    db.flush()
    db.add_all(default_permissions(user))
    db.commit()
    user_id = user.id
    db.close()
    return RedirectResponse(f"/admin/users/{user_id}/permissions?created=1", status_code=303)


@router.get("/admin/users/{user_id}/permissions")
def user_permissions_page(request: Request, user_id: int):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return RedirectResponse("/admin?error=user", status_code=303)
        rows = {row.section: row for row in db.query(UserPermission).filter_by(user_id=user_id).all()}
        return templates.TemplateResponse("admin_permissions.html", {
            "request": request, "account": user, "permissions": rows,
            "sections": SECTIONS, "actions": ACTIONS, "roles": ROLE_LABELS,
        })
    finally:
        db.close()


@router.post("/admin/users/{user_id}/permissions")
async def save_user_permissions(request: Request, user_id: int):
    form = await request.form()
    role = str(form.get("role", ""))
    if role not in ROLE_LABELS:
        return RedirectResponse(f"/admin/users/{user_id}/permissions?error=role", status_code=303)
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return RedirectResponse("/admin?error=user", status_code=303)
        if user.id == request.session.get("user_id") and role != "admin":
            return RedirectResponse(f"/admin/users/{user_id}/permissions?error=self", status_code=303)
        user.role = role
        rows = {row.section: row for row in db.query(UserPermission).filter_by(user_id=user_id).all()}
        for section in SECTIONS:
            row = rows.get(section)
            if row is None:
                row = UserPermission(user_id=user_id, section=section)
                db.add(row)
            for action in ACTIONS:
                enabled = f"{section}.{action}" in form
                if role == "viewer" and action not in {"view", "download"}:
                    enabled = False
                setattr(row, f"can_{action}", enabled)
        db.commit()
    finally:
        db.close()
    return RedirectResponse(f"/admin/users/{user_id}/permissions?saved=1", status_code=303)


@router.post("/admin/users/{user_id}/reset-password")
def reset_password(user_id: int, initial_password: str = Form(...)):
    if len(initial_password) < 8:
        return RedirectResponse("/admin?error=password", status_code=303)
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.password = pwd_context.hash(initial_password)
        user.force_password_change = True
        db.commit()
    db.close()
    return RedirectResponse("/admin?success=reset", status_code=303)


@router.post("/admin/users/{user_id}/team")
def update_user_team(user_id: int, team: str = Form(...)):
    if team not in TEAM_LABELS:
        return RedirectResponse("/admin?error=team", status_code=303)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return RedirectResponse("/admin?error=user", status_code=303)
        user.team = team
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/admin?success=team", status_code=303)


@router.post("/admin/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    if user_id == request.session.get("user_id"):
        return RedirectResponse("/admin?error=self", status_code=303)
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.query(UserPermission).filter_by(user_id=user_id).delete()
        db.delete(user)
        db.commit()
    db.close()
    return RedirectResponse("/admin?success=deleted", status_code=303)


@router.post("/admin/products")
def create_product(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    profile: str = Form("pro"),
    jira_project: str = Form(""),
):
    code = code.strip().lower()
    name = name.strip()
    jira_project = jira_project.strip().upper()
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", code)
        or not 1 <= len(name) <= 60
        or profile not in {"pro", "bp"}
        or (jira_project and not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,19}", jira_project))
    ):
        return templates.TemplateResponse(
            "admin.html",
            admin_context(request, error="제품 코드·이름·업무 양식·Jira 프로젝트를 확인하세요."),
            status_code=400,
        )
    db = SessionLocal()
    try:
        if db.query(OperatingProduct.id).filter_by(code=code).first():
            return templates.TemplateResponse(
                "admin.html",
                admin_context(request, error="이미 등록된 제품 코드입니다."),
                status_code=409,
            )
        next_order = max((row.sort_order for row in db.query(OperatingProduct.sort_order).all()), default=0) + 1
        db.add(OperatingProduct(
            code=code, name=name, profile=profile,
            jira_project=jira_project or None, sort_order=next_order,
        ))
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/admin?success=product#products", status_code=303)