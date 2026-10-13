import asyncio
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.middleware import AuthorizationMiddleware, action_for_request, section_for_path
from app.models.user import User
from app.security import ROLE_LABELS, TEAM_LABELS
from app.services.permissions import default_permissions


class AccountPermissionsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.sessions = sessionmaker(bind=engine)
        db = self.sessions()
        self.user = User(username="tester", password="unused", team="cs", role="user")
        db.add(self.user)
        db.flush()
        db.add_all(default_permissions(self.user))
        db.commit()
        self.user_id = self.user.id
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def check(self, path, method="GET"):
        async def endpoint(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        scope = {
            "type": "http", "method": method, "scheme": "http", "server": ("test", 80),
            "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
            "session": {"user": "tester", "user_id": self.user_id, "role": "user"},
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        with patch("app.middleware.SessionLocal", self.sessions):
            asyncio.run(AuthorizationMiddleware(endpoint)(scope, receive, send))
        return next(item["status"] for item in messages if item["type"] == "http.response.start")

    def test_roles_and_teams(self):
        self.assertEqual(list(ROLE_LABELS), ["admin", "user", "viewer"])
        self.assertEqual(list(TEAM_LABELS.values()), ["서비스운영", "품질", "물류", "연구개발", "CS"])

    def test_default_team_and_account_override(self):
        self.assertEqual(self.check("/cart_bp_pro/cx/update/1", "POST"), 200)
        self.assertEqual(self.check("/cart_bp_pro/exchange/update/1", "POST"), 403)
        db = self.sessions()
        from app.models.user import UserPermission
        row = db.query(UserPermission).filter_by(user_id=self.user_id, section="exchange").one()
        row.can_edit = True
        db.commit()
        db.close()
        self.assertEqual(self.check("/cart_bp_pro/exchange/update/1", "POST"), 200)

    def test_action_classification(self):
        self.assertEqual(section_for_path("/cart_bp_pro/api/result/master/1"), "result")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/cx/slack-preview"), "view")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/cx/slack-import"), "slack_import")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/cx/excel-upload"), "excel_upload")
        self.assertEqual(action_for_request("GET", "/cart_bp_pro/cx/excel-download"), "download")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/cx/create"), "create")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/lab/upload/123"), "file_upload")
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/exchange/transfer-requests/1/review"), "approve")
        self.assertEqual(action_for_request("DELETE", "/cart_bp_pro/cx/case/1"), "delete")

    def test_viewer_cannot_write(self):
        db = self.sessions()
        db.query(User).filter_by(id=self.user_id).one().role = "viewer"
        db.commit()
        db.close()
        self.assertEqual(self.check("/cart_bp_pro/cx", "GET"), 200)
        self.assertEqual(self.check("/cart_bp_pro/cx/excel-download", "GET"), 200)
        self.assertEqual(self.check("/cart_bp_pro/cx/update/1", "POST"), 403)
        db = self.sessions()
        from app.models.user import UserPermission
        db.query(UserPermission).filter_by(user_id=self.user_id, section="cx").one().can_download = False
        db.commit()
        db.close()
        self.assertEqual(self.check("/cart_bp_pro/cx/excel-download", "GET"), 403)

    def test_upload_and_edit_are_independent(self):
        db = self.sessions()
        from app.models.user import UserPermission
        row = db.query(UserPermission).filter_by(user_id=self.user_id, section="cx").one()
        row.can_excel_upload = False
        row.can_slack_import = False
        db.commit()
        db.close()
        self.assertEqual(self.check("/cart_bp_pro/cx/update/1", "POST"), 200)
        self.assertEqual(self.check("/cart_bp_pro/cx/excel-upload", "POST"), 403)
        self.assertEqual(self.check("/cart_bp_pro/cx/slack-import", "POST"), 403)


if __name__ == "__main__":
    unittest.main()