from datetime import datetime
from urllib.parse import quote


def dated_excel_disposition(product: str, label: str) -> str:
    """Return an RFC 5987-compatible attachment header for a dated Excel file."""
    from app.services.products import product_name

    product_code = str(product or "").strip()
    display_name = product_name(product_code.lower())
    safe_product = display_name.replace("/", "／").replace("\\", "＼")
    filename = f"{safe_product}_{label}_{datetime.now().date().isoformat()}.xlsx"
    ascii_fallback = f"{safe_product.encode('ascii', 'ignore').decode() or 'product'}_{datetime.now().date().isoformat()}.xlsx"
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )