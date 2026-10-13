from app.database import SessionLocal
from app.models.user import User, UserPermission

SECTIONS = {
    "dashboard": "대시보드",
    "cx": "CX 분석",
    "exchange": "교품/회수",
    "quality": "품질 분석",
    "lab": "연구소 분석",
    "result": "최종 결과",
}
ACTIONS = {
    "view": "조회",
    "download": "Excel 다운로드",
    "create": "등록",
    "edit": "수정",
    "excel_upload": "Excel 업로드",
    "slack_import": "Slack 가져오기",
    "file_upload": "첨부 업로드",
    "delete": "삭제",
    "approve": "승인·반려",
}
TEAM_SECTION = {
    "cs": "cx", "logistics": "exchange", "quality": "quality",
    "rnd": "lab", "service_ops": "result",
}


def default_permissions(user, legacy_role=None):
    """Preserve the old team-based rights when migrating existing accounts."""
    old_role = legacy_role or user.role
    owned = {TEAM_SECTION.get(user.team)}
    # Service operations could previously edit exchange before the logistics split.
    if old_role in {"manager", "editor"} and user.team == "service_ops":
        owned.add("exchange")
    if old_role != "viewer":
        owned.add("dashboard")
    can_write = old_role != "viewer"
    return [
        UserPermission(
            user_id=user.id, section=section, can_view=True,
            can_download=True,
            can_create=old_role == "admin" or (can_write and section in owned),
            can_edit=old_role == "admin" or (can_write and section in owned),
            can_excel_upload=old_role == "admin" or (can_write and section in owned),
            can_slack_import=old_role == "admin" or (can_write and section in owned),
            can_file_upload=old_role == "admin" or (can_write and section in owned),
            can_import=old_role == "admin" or (can_write and section in owned),
            can_delete=old_role == "admin" or (old_role == "manager" and section in owned),
            can_approve=old_role == "admin" or (old_role == "manager" and section in owned),
        )
        for section in SECTIONS
    ]


def seed_user_permissions():
    db = SessionLocal()
    try:
        for user in db.query(User).all():
            old_role = user.role
            if not db.query(UserPermission.id).filter_by(user_id=user.id).first():
                db.add_all(default_permissions(user, old_role))
            if old_role in {"manager", "editor"}:
                user.role = "user"
        db.commit()
    finally:
        db.close()


def account_has_permission(db, user_id, section, action):
    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        return False
    if user.role == "admin":
        return True
    if user.role == "viewer" and action not in {"view", "download"}:
        return False
    row = db.query(UserPermission).filter_by(user_id=user.id, section=section).first()
    return bool(row and row.can_view and getattr(row, f"can_{action}", False))