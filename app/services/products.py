from app.database import SessionLocal
from app.models.product import OperatingProduct


BUILTIN_PRODUCTS = (
    ("cart_bp_pro", "CART BP pro", "pro", "CART", 0),
    ("cart_bp", "CART BP", "bp", "BP", 1),
    ("cart_on", "CART ON", "pro", None, 2),
)


def seed_products():
    db = SessionLocal()
    try:
        existing = {row.code for row in db.query(OperatingProduct.code).all()}
        for code, name, profile, jira_project, sort_order in BUILTIN_PRODUCTS:
            if code not in existing:
                db.add(OperatingProduct(
                    code=code, name=name, profile=profile,
                    jira_project=jira_project, sort_order=sort_order,
                ))
        db.commit()
    finally:
        db.close()


def list_products():
    db = SessionLocal()
    try:
        return [
            {
                "code": row.code,
                "name": row.name,
                "profile": row.profile,
                "jira_project": row.jira_project,
            }
            for row in db.query(OperatingProduct)
            .order_by(OperatingProduct.sort_order, OperatingProduct.id).all()
        ]
    finally:
        db.close()


def get_product(code):
    db = SessionLocal()
    try:
        row = db.query(OperatingProduct).filter_by(code=code).first()
        return (
            {"code": row.code, "name": row.name, "profile": row.profile,
             "jira_project": row.jira_project}
            if row else None
        )
    finally:
        db.close()


def product_name(code):
    product = get_product(code)
    return product["name"] if product else str(code or "Product")


def product_profile(code):
    product = get_product(code)
    return product["profile"] if product else "pro"


def is_bp_product(code):
    return product_profile(code) == "bp"