import asyncio
import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request
from app.database import Base
from app.middleware import action_for_request
from app.models.server_incident import ServerIncident
from app.routers.dashboard import (
    create_server_incident,
    delete_server_incident,
    parse_server_incident,
    update_server_incident,
)


class ServerIncidentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.payload = {
            "occurred_at": "2026-09-17T14:02", "duration_minutes": 12,
            "reason": "서버 재시작", "cs_count": 3,
            "cs_details": "로그인 실패 2건\n조회 지연 1건",
            "rnd_status": "진행 중", "rnd_response": "로그 분석",
            "business_status": "대응 완료", "business_response": "고객 안내",
        }

    def tearDown(self):
        self.engine.dispose()

    def request(self, payload):
        body = json.dumps(payload).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""}, receive)

    def test_create_update_delete_product_scoped(self):
        with patch("app.routers.dashboard.SessionLocal", self.sessions), patch("app.routers.dashboard.get_product", return_value={"code": "cart_bp_pro"}):
            created = asyncio.run(create_server_incident("cart_bp_pro", self.request(self.payload)))
            incident_id = created["id"]
            db = self.sessions()
            row = db.query(ServerIncident).filter_by(id=incident_id).one()
            self.assertEqual(row.occurred_at.isocalendar().week, 38)
            self.assertEqual(row.cs_count, 3)
            db.close()

            changed = dict(self.payload, cs_count=5, business_response="추가 안내")
            asyncio.run(update_server_incident("cart_bp_pro", incident_id, self.request(changed)))
            db = self.sessions()
            self.assertEqual(db.query(ServerIncident).filter_by(id=incident_id).one().cs_count, 5)
            db.close()

            with self.assertRaises(HTTPException) as error:
                delete_server_incident("cart_on", incident_id)
            self.assertEqual(error.exception.status_code, 404)
            delete_server_incident("cart_bp_pro", incident_id)
            db = self.sessions()
            self.assertEqual(db.query(ServerIncident).count(), 0)
            db.close()

    def test_validation_and_permissions(self):
        with self.assertRaises(HTTPException):
            parse_server_incident(dict(self.payload, duration_minutes=-1))
        self.assertEqual(action_for_request("POST", "/cart_bp_pro/dashboard/uptime-incidents"), "create")
        self.assertEqual(action_for_request("PUT", "/cart_bp_pro/dashboard/uptime-incidents/1"), "edit")
        self.assertEqual(action_for_request("DELETE", "/cart_bp_pro/dashboard/uptime-incidents/1"), "delete")


if __name__ == "__main__":
    unittest.main()