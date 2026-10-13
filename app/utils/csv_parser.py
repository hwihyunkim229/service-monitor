import csv
from io import BytesIO, StringIO
import pandas as pd


def parse_lot_csv(file):
    raw = file.file.read()

    if (getattr(file, "filename", "") or "").lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(BytesIO(raw), dtype=object)
        lot_key = next(
            (col for col in frame.columns if "lot" in str(col).strip().lower()), None
        )
        qty_key = next(
            (
                col
                for col in frame.columns
                if "등록" in str(col) or "quantity" in str(col).strip().lower()
            ),
            None,
        )
        if lot_key is None or qty_key is None:
            raise ValueError(
                "Excel 컬럼을 확인해 주세요. LOT와 등록량 컬럼이 필요합니다."
            )
        return [
            {"lot": str(row[lot_key]).strip(), "quantity": int(row[qty_key])}
            for _, row in frame.iterrows()
            if not pd.isna(row[lot_key]) and str(row[lot_key]).strip()
        ]

    for encoding in ["utf-8", "cp949", "euc-kr"]:
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("지원하지 않는 파일 인코딩입니다.")

    reader = csv.DictReader(StringIO(content))

    lot_key = None
    qty_key = None

    for col in reader.fieldnames:
        col_clean = col.strip().lower()

        if "lot" in col_clean:
            lot_key = col
        if "등록량" in col or "quantity" in col_clean:
            qty_key = col

    if not lot_key or not qty_key:
        raise ValueError("CSV 컬럼 형식이 올바르지 않습니다. (LOT, 등록량 필요)")

    data = []
    for row in reader:
        lot = row[lot_key].strip()
        qty = int(row[qty_key])

        if not lot:
            continue

        data.append({"lot": lot, "quantity": qty})

    return data