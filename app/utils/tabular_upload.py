from io import BytesIO, StringIO
import csv
import pandas as pd


def read_rows(contents: bytes, filename: str | None = None):
    """Return spreadsheet or CSV content as positional string rows."""
    if (filename or "").lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(BytesIO(contents), header=None, dtype=object)
        return [
            [None if pd.isna(value) else str(value) for value in row]
            for row in frame.values.tolist()
        ]
    try:
        decoded = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = contents.decode("cp949")
    return list(csv.reader(StringIO(decoded)))


def read_dict_rows(contents: bytes, filename: str | None = None):
    """Return spreadsheet or CSV content as header-keyed rows."""
    if (filename or "").lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(BytesIO(contents), dtype=object)
        return [
            {
                str(key): None if pd.isna(value) else str(value)
                for key, value in row.items()
            }
            for row in frame.to_dict(orient="records")
        ]
    try:
        decoded = contents.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = contents.decode("cp949")
    return list(csv.DictReader(StringIO(decoded)))