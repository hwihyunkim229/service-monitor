from starlette.responses import JSONResponse, RedirectResponse

from app.database import SessionLocal
from app.models.user import User, UserPermission
from app.services.permissions import SECTIONS
from app.slack_auth import verify_slack_token

PUBLIC_PREFIXES = ("/login", "/logout", "/change-password", "/static", "/uploads")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GLOBAL_DASHBOARD_PATHS = {"dashboard", "upload-csv", "get-loss-data", "save-loss", "save-uptime", "lot"}


def section_for_path(path):
    parts = path.strip("/").split("/")
    if parts[0] in GLOBAL_DASHBOARD_PATHS:
        return "dashboard"
    if len(parts) >= 3 and parts[1] == "api" and parts[2] in SECTIONS:
        return parts[2]
    if len(parts) >= 2 and parts[1] in SECTIONS:
        return parts[1]
    return None


def action_for_request(method, path):
    if path.endswith(("/slack-preview", "/dashboard/loss-reasons")) and method == "POST":
        return "view"
    if method not in WRITE_METHODS:
        return "download" if path.endswith(("/excel", "/excel-download", "/excel-legacy", "/export")) else "view"
    if method == "DELETE" or path.endswith("/delete-file") or "/delete" in path:
        return "delete"
    if any(token in path for token in ("/review", "/approve", "/reject")):
        return "approve"
    if path.endswith("/slack-import"):
        return "slack_import"
    if any(token in path for token in ("/excel-upload", "/upload_csv", "/process-rev-upload", "/report-upload", "/upload-csv", "/lot/upload")):
        return "excel_upload"
    if "/lab/upload/" in path:
        return "file_upload"
    if method == "POST" and path.endswith("/dashboard/uptime-incidents"):
        return "create"
    if any(token in path for token in ("/create", "/create-empty", "/create-multiple", "/transfer-request")):
        return "create"
    return "edit"


class AuthorizationMiddleware:
    """Enforce the saved account-level permissions on every request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope, receive=receive)
        path = request.url.path
        if (request.method == "POST" and path.endswith(("/cx/slack-preview", "/cx/slack-import"))
                and verify_slack_token(request.headers.get("x-slack-token", ""))):
            scope.setdefault("state", {})["slack_token_authenticated"] = True
            await self.app(scope, receive, send)
            return
        if path.startswith(PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return
        session = request.session
        if not session.get("user"):
            if request.method in WRITE_METHODS:
                await JSONResponse({"detail": "로그인 세션이 만료되었습니다. 다시 로그인해 주세요."}, status_code=401)(scope, receive, send)
            else:
                await RedirectResponse("/login", status_code=303)(scope, receive, send)
            return
        if session.get("force_password_change"):
            await RedirectResponse("/change-password", status_code=303)(scope, receive, send)
            return

        db = SessionLocal()
        try:
            user = db.query(User).filter_by(id=session.get("user_id")).first()
            if not user:
                allowed, message = False, "계정을 찾을 수 없습니다. 다시 로그인해 주세요."
            elif user.role == "admin":
                allowed, message = True, ""
            elif path.startswith("/admin"):
                allowed, message = False, "관리자만 계정 및 권한 설정에 접근할 수 있습니다."
            else:
                section = section_for_path(path)
                action = action_for_request(request.method, path)
                if not section:
                    allowed = request.method not in WRITE_METHODS
                    message = "이 작업의 권한 범위를 확인할 수 없습니다."
                elif user.role == "viewer" and action not in {"view", "download"}:
                    allowed, message = False, "뷰어는 데이터를 변경할 수 없습니다."
                else:
                    permission = db.query(UserPermission).filter_by(user_id=user.id, section=section).first()
                    allowed = bool(permission and permission.can_view and getattr(permission, f"can_{action}"))
                    message = f"{SECTIONS[section]}의 {action} 권한이 없습니다."
        finally:
            db.close()
        if not allowed:
            await JSONResponse({"detail": message}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)