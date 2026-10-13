import gspread
from google.oauth2.service_account import Credentials
from app.database import SessionLocal
from app.models.cs import CSCase
from datetime import datetime
import os
from pathlib import Path
import re
import time
from threading import Event, Lock

SERVICE_ACCOUNT_FILE = Path(
    os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        Path(__file__).resolve().parents[2] / "service-account.json",
    )
)
SPREADSHEET_ID = os.getenv("GOOGLE_SPREADSHEET_ID", "")

last_sync_time = 0
SYNC_INTERVAL = 300
GOOGLE_API_TIMEOUT = (10, 30)
_sync_lock = Lock()


def parse_date(date_str, *, log_failure=True):
    """여러 날짜 포맷 대응"""
    if date_str is None:
        return None

    normalized = str(date_str).strip().replace(".", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("-/", "/").replace("/-", "/")
    patterns = (
        (r"(?<!\d)(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})(?!\d)", True),
        (r"(?<!\d)(\d{2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})(?!\d)", True),
        (r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", False),
        (r"(?<!\d)(\d{1,2})\s*[-/]\s*(?:-\s*)?(\d{1,2})(?!\d)", False),
    )
    for pattern, has_year in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        try:
            if has_year:
                year, month, day = map(int, match.groups())
                if year < 100:
                    year += 2000
            else:
                month, day = map(int, match.groups())
                year = datetime.now().year
            return datetime(year, month, day)
        except ValueError:
            continue

    if log_failure:
        print("날짜 변환 실패:", normalized)
    return None


def collect_sheet_values(rows, transfer_idx, date_idx, replacement_serial_idx=9):
    """Collect return dates and replacement serial numbers by transfer number."""
    return_dates = {}
    replacement_serials = {}
    for row in rows[6:]:
        try:
            transfer_no = row[transfer_idx].strip()
        except (IndexError, AttributeError):
            continue

        if not transfer_no:
            continue

        try:
            return_date = row[date_idx].strip()
        except (IndexError, AttributeError):
            return_date = ""

        try:
            replacement_serial = row[replacement_serial_idx].strip()
        except (IndexError, AttributeError):
            replacement_serial = ""
        if replacement_serial_idx != date_idx:
            replacement_serials[transfer_no] = replacement_serial

        date_obj = (
            parse_date(
                return_date,
                log_failure=replacement_serial_idx != date_idx,
            )
            if return_date
            else None
        )

        if (
            replacement_serial_idx == date_idx
            and replacement_serial
            and not date_obj
        ):
            replacement_serials[transfer_no] = replacement_serial
        if not date_obj:
            continue

        return_dates[transfer_no] = date_obj

    return return_dates, replacement_serials


def collect_return_dates(rows, transfer_idx, date_idx):
    """Backward-compatible helper used by existing callers and tests."""
    return collect_sheet_values(rows, transfer_idx, date_idx)[0]


def sync_return_date():

    global last_sync_time

    now = time.time()

    if now - last_sync_time < SYNC_INTERVAL:
        return 0

    if not _sync_lock.acquire(blocking=False):
        return 0

    db = None
    client = None
    try:
        print("Google Sheet 동기화 실행")

        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=scopes
        )

        client = gspread.authorize(creds)
        client.set_timeout(GOOGLE_API_TIMEOUT)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet_config = [
            ("교품정보시트", "cart_bp_pro", 0, 11, 9),
            ("교품정보시트(CART BP)", "cart_bp", 1, 9, 11),
        ]
        return_dates = {}
        replacement_serials = {}
        for (
            sheet_name,
            product,
            transfer_idx,
            date_idx,
            replacement_serial_idx,
        ) in sheet_config:
            try:
                rows = spreadsheet.worksheet(sheet_name).get_all_values()
                worksheet_dates, worksheet_serials = collect_sheet_values(
                    rows,
                    transfer_idx,
                    date_idx,
                    replacement_serial_idx,
                )
                return_dates.update(
                    {
                        (product, transfer_no): return_date
                        for transfer_no, return_date in worksheet_dates.items()
                    }
                )
                replacement_serials.update(
                    {
                        (product, transfer_no): serial_no
                        for transfer_no, serial_no in worksheet_serials.items()
                    }
                )
            except Exception as e:
                print(f"{sheet_name} 처리 실패:", e)

        if not return_dates and not replacement_serials:
            return 0

        db = SessionLocal()
        sheet_keys = set(return_dates) | set(replacement_serials)
        products = {product for product, _ in sheet_keys}
        transfer_numbers = {transfer_no for _, transfer_no in sheet_keys}
        cases = (
            db.query(CSCase)
            .filter(
                CSCase.product.in_(products),
                CSCase.transfer_no.in_(transfer_numbers),
            )
            .all()
        )

        updated_return_dates = 0
        updated_replacement_serials = 0
        for case in cases:
            key = (case.product, case.transfer_no)
            return_date = return_dates.get(key)
            if not case.return_at_manual and return_date and case.return_at != return_date:
                case.return_at = return_date
                updated_return_dates += 1

            replacement_serial = replacement_serials.get(key)
            if (
                not case.replacement_serial_no_manual
                and key in replacement_serials
                and (case.replacement_serial_no or "") != replacement_serial
            ):
                case.replacement_serial_no = replacement_serial or None
                updated_replacement_serials += 1

        if updated_return_dates or updated_replacement_serials:
            db.commit()
        last_sync_time = now
        print(
            f"스프레드시트 동기화 완료 : 회수일 {updated_return_dates}건, "
            f"교체 S/N {updated_replacement_serials}건"
        )
        return updated_return_dates + updated_replacement_serials
    except Exception as e:
        if db is not None:
            db.rollback()
        print("Google Sheet 동기화 실패:", e)
        return 0
    finally:
        if db is not None:
            db.close()
        if client is not None:
            try:
                client.http_client.session.close()
            except Exception as e:
                print("Google Sheet HTTP 세션 종료 실패:", e)
        _sync_lock.release()


def run_sync_worker(stop_event: Event):
    """Keep Sheet I/O out of request handling."""
    while not stop_event.is_set():
        sync_return_date()
        stop_event.wait(SYNC_INTERVAL)
