from contextlib import asynccontextmanager
import json
import os
from threading import Event, Thread
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware
load_dotenv()
from app.database import Base, engine
from app.middleware import AuthorizationMiddleware
from app.models.user import User, UserPermission
from app.services.permissions import seed_user_permissions
from app.models.cs import CSCase
from app.models.cx import CXAnalysis
from app.models.lot_process_rev import LotProcessRev
from app.models.product import OperatingProduct
from app.models.server_incident import ServerIncident
from app.services.process_rev import seed_process_rev_master
from app.services.products import get_product, list_products, seed_products
from app.routers.sheet_sync import run_sync_worker
from app.routers import (
    auth,
    admin,
    dashboard,
    device_search,
    exchange,
    quality,
    lab,
    lot,
    result,
    renewal,
    workspace,
)

Base.metadata.create_all(bind=engine)
seed_products()
seed_process_rev_master()

with engine.begin() as connection:
    connection.execute(text("PRAGMA journal_mode=WAL"))
    connection.execute(
        text(
            "UPDATE cs_case "
            "SET lot = UPPER(CASE "
            "WHEN INSTR(serial_no, '-') > 0 "
            "THEN SUBSTR(serial_no, 1, INSTR(serial_no, '-') - 1) "
            "ELSE serial_no END) "
            "WHERE serial_no IS NOT NULL AND TRIM(serial_no) <> '' "
            "AND (lot IS NULL OR TRIM(lot) = '')"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_cs_case_product_date_id "
            "ON cs_case (product, date_only, id)"
        )
    )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_cs_case_product_week_date "
            "ON cs_case (product, week, date_only)"
        )
    )
    connection.execute(
        text(
            "UPDATE cs_case SET week = MIN("
            "CAST(STRFTIME('%W', COALESCE(date_only, received_at)) AS INTEGER) + 1, "
            "CAST(STRFTIME('%W', "
            "STRFTIME('%Y', COALESCE(date_only, received_at)) || '-12-28'"
            ") AS INTEGER) + 1"
            ") WHERE COALESCE(date_only, received_at) IS NOT NULL"
        )
    )


def ensure_columns(table, definitions):
    with engine.begin() as connection:
        existing = {
            row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
        }
        for name, sql_type in definitions.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                )


ensure_columns(
    "users",
    {
        "team": "VARCHAR DEFAULT 'service_ops'",
        "role": "VARCHAR DEFAULT 'viewer'",
        "force_password_change": "BOOLEAN DEFAULT 0",
    },
)


def ensure_permission_columns():
    legacy_sources = {
        "can_download": "can_view",
        "can_create": "can_edit",
        "can_excel_upload": "can_import",
        "can_slack_import": "can_import",
        "can_file_upload": "can_import",
    }
    with engine.begin() as connection:
        existing = {
            row[1] for row in connection.execute(text("PRAGMA table_info(user_permissions)"))
        }
        for column, source in legacy_sources.items():
            if column not in existing:
                connection.execute(text(f"ALTER TABLE user_permissions ADD COLUMN {column} BOOLEAN DEFAULT 0 NOT NULL"))
                connection.execute(text(f"UPDATE user_permissions SET {column} = {source}"))


ensure_permission_columns()
seed_user_permissions()
ensure_columns(
    "cs_case",
    {
        "cs_started_at": "DATETIME",
        "cs_completed_at": "DATETIME",
        "return_at_manual": "BOOLEAN DEFAULT 0",
        "replacement_serial_no_manual": "BOOLEAN DEFAULT 0",
    },
)
ensure_columns("return_transfer_request", {"urgent": "BOOLEAN DEFAULT 0"})
ensure_columns(
    "rnd_transfer_request",
    {
        "request_type": "VARCHAR DEFAULT 'set' NOT NULL",
        "urgent": "BOOLEAN DEFAULT 0 NOT NULL",
    },
)
ensure_columns("quality_inspection", {"return_transfer_date": "DATE"})
ensure_columns(
    "cs_case",
    {
        "hospital_name": "VARCHAR",
        "rev": "VARCHAR",
        "seller": "VARCHAR",
        "invoice_no": "VARCHAR",
        "replacement_serial_no": "VARCHAR",
    },
)
ensure_columns(
    "cx_analysis",
    {
        "cx_yn": "VARCHAR",
        "rnd_yn": "VARCHAR",
        "rnd_team": "VARCHAR",
        "received_at": "DATETIME",
        "processed_at": "DATETIME",
        "data_loss_type": "VARCHAR",
        "data_loss_count": "INTEGER",
    },
)
ensure_columns(
    "server_downtime",
    {
        "year": "INTEGER",
        "reason": "TEXT",
        "updated_at": "DATETIME",
    },
)


@asynccontextmanager
async def lifespan(app):
    sheet_sync_stop = Event()
    sheet_sync_thread = Thread(
        target=run_sync_worker,
        args=(sheet_sync_stop,),
        name="google-sheet-return-date-sync",
        daemon=True,
    )
    sheet_sync_thread.start()
    try:
        yield
    finally:
        sheet_sync_stop.set()
        sheet_sync_thread.join(timeout=2)


app = FastAPI(title="Service Monitor Renewal", lifespan=lifespan)
app.state.product_catalog = list_products
app.state.product_config = get_product
app.add_middleware(AuthorizationMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "service-monitor-local-development-only"),
    session_cookie="service_monitor_renewal_session",
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["from_json"] = lambda value: json.loads(value) if value else []
for router in (
    auth.router,
    admin.router,
    dashboard.router,
    device_search.router,
    renewal.router,
    exchange.router,
    quality.router,
    lab.router,
    lot.router,
    result.router,
    workspace.router,
):
    app.include_router(router)
