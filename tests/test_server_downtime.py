import asyncio
from fastapi import HTTPException
from app.models.server_downtime import ServerDowntime
from app.routers import dashboard


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class FakeQuery:
    def __init__(self, row=None):
        self.row = row

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.row


class FakeSession:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.committed = False
        self.closed = False

    def query(self, *args):
        return FakeQuery(self.row)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def call_save(payload, session):
    original = dashboard.SessionLocal
    dashboard.SessionLocal = lambda: session
    try:
        return asyncio.run(dashboard.save_uptime(FakeRequest(payload)))
    finally:
        dashboard.SessionLocal = original


def test_save_uptime_creates_weekly_reason():
    session = FakeSession()
    response = call_save(
        {
            "product": "cart_bp_pro",
            "year": 2026,
            "week": 34,
            "downtime": 17,
            "reason": "배포 이후 API 응답 지연",
        },
        session,
    )

    row = next(item for item in session.added if isinstance(item, ServerDowntime))
    assert response.status_code == 200
    assert row.year == 2026
    assert row.week == 34
    assert row.downtime_minutes == 17
    assert row.reason == "배포 이후 API 응답 지연"
    assert session.committed and session.closed


def test_save_uptime_updates_legacy_row_without_losing_minutes():
    row = ServerDowntime(product="cart_bp_pro", week=28, downtime_minutes=15, year=None)
    session = FakeSession(row)
    call_save(
        {
            "product": "cart_bp_pro",
            "year": 2026,
            "week": 28,
            "downtime": 15,
            "reason": "기존 15분 장애의 원인 기록",
        },
        session,
    )

    assert row.year == 2026
    assert row.downtime_minutes == 15
    assert row.reason == "기존 15분 장애의 원인 기록"


def test_save_uptime_rejects_invalid_minutes():
    try:
        call_save(
            {
                "product": "cart_bp_pro",
                "year": 2026,
                "week": 34,
                "downtime": 10081,
                "reason": "잘못된 입력",
            },
            FakeSession(),
        )
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError("invalid downtime should be rejected")