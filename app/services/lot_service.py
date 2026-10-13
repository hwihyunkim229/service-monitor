from sqlalchemy.orm import Session
from datetime import datetime
from app.models.lot import LotInventory, LotCurrent
from app.models.cs import CSCase
from sqlalchemy import case, func
from app.models.lot import LotDefectManual


def upload_lot_data(db: Session, data: list, week=None):
    updated, created = 0, 0

    for row in data:
        lot = row["lot"]
        qty = row["quantity"]

        db.add(LotInventory(lot=lot, quantity=qty, week=week))

        existing = db.query(LotCurrent).filter_by(lot=lot).first()

        if existing:
            existing.quantity = qty
            existing.last_updated = datetime.utcnow()
            updated += 1
        else:
            db.add(LotCurrent(lot=lot, quantity=qty))
            created += 1

    db.commit()

    return {"updated": updated, "created": created}


def get_lot_stats(db):

    lot_rows = (
        db.query(
            LotCurrent.lot,
            LotCurrent.quantity,
            func.count(CSCase.id).label("cs_count"),
            func.coalesce(
                func.sum(case((CSCase.exchange_type == "O", 1), else_=0)), 0
            ).label("defect_count"),
        )
        .outerjoin(CSCase, CSCase.serial_no.like(LotCurrent.lot + "%"))
        .group_by(LotCurrent.lot, LotCurrent.quantity)
        .all()
    )

    manual_map = {m.lot: m for m in db.query(LotDefectManual).all()}
    result = []

    for lot_name, quantity, cs_count_real, defect_count_real in lot_rows:
        manual = manual_map.get(lot_name)

        cs_count_manual = manual.cs_count if manual else 0
        defect_count_manual = manual.defect_count if manual else 0

        cs_total = cs_count_real + cs_count_manual
        defect_total = defect_count_real + defect_count_manual

        defect_rate = round(defect_total / quantity * 100, 2) if quantity else 0

        result.append(
            {
                "lot": lot_name,
                "quantity": quantity,
                "cs_count": cs_total,
                "defect_count": defect_total,
                "defect_rate": defect_rate,
            }
        )

    return result


def get_all_lots(db: Session):
    return db.query(LotCurrent).all()


def insert_manual_data(db):

    data = [
        {"lot": "F250404", "cs": 6, "defect": 4},
        {"lot": "F250601", "cs": 24, "defect": 6},
        {"lot": "F250602", "cs": 7, "defect": 4},
        {"lot": "F250603", "cs": 34, "defect": 14},
        {"lot": "F250604", "cs": 12, "defect": 3},
        {"lot": "F250605", "cs": 29, "defect": 8},
        {"lot": "F250606", "cs": 39, "defect": 8},
        {"lot": "F250607", "cs": 14, "defect": 5},
        {"lot": "F250608", "cs": 22, "defect": 9},
        {"lot": "F250609", "cs": 16, "defect": 9},
        {"lot": "F250610", "cs": 21, "defect": 4},
        {"lot": "F250611", "cs": 27, "defect": 11},
        {"lot": "F250615", "cs": 13, "defect": 7},
        {"lot": "F250616", "cs": 28, "defect": 13},
        {"lot": "F250617", "cs": 31, "defect": 7},
        {"lot": "F250618", "cs": 19, "defect": 7},
        {"lot": "F250701", "cs": 45, "defect": 20},
        {"lot": "F250702", "cs": 27, "defect": 14},
        {"lot": "F250703", "cs": 27, "defect": 11},
        {"lot": "F250704", "cs": 67, "defect": 29},
        {"lot": "F251002", "cs": 31, "defect": 5},
        {"lot": "F251103", "cs": 9, "defect": 2},
        {"lot": "F251104", "cs": 12, "defect": 2},
        {"lot": "F251105", "cs": 12, "defect": 4},
        {"lot": "F251106", "cs": 22, "defect": 2},
        {"lot": "F251107", "cs": 16, "defect": 3},
        {"lot": "F251108", "cs": 3, "defect": 0},
        {"lot": "F251109", "cs": 19, "defect": 2},
        {"lot": "F251110", "cs": 25, "defect": 2},
        {"lot": "F251111", "cs": 10, "defect": 1},
        {"lot": "F251112", "cs": 25, "defect": 2},
        {"lot": "F251113", "cs": 5, "defect": 1},
        {"lot": "F251114", "cs": 8, "defect": 5},
        {"lot": "F251115", "cs": 13, "defect": 1},
        {"lot": "F251116", "cs": 9, "defect": 1},
        {"lot": "F251117", "cs": 13, "defect": 4},
        {"lot": "F251118", "cs": 5, "defect": 2},
        {"lot": "F251119", "cs": 15, "defect": 4},
        {"lot": "F251120", "cs": 17, "defect": 4},
        {"lot": "F251121", "cs": 30, "defect": 3},
        {"lot": "F251205", "cs": 3, "defect": 1},
        {"lot": "F251206", "cs": 2, "defect": 0},
        {"lot": "F251207", "cs": 2, "defect": 2},
        {"lot": "F251312", "cs": 21, "defect": 6},
        {"lot": "F251316", "cs": 1, "defect": 0},
        {"lot": "F251317", "cs": 1, "defect": 1},
        {"lot": "F251402", "cs": 2, "defect": 1},
        {"lot": "F251406", "cs": 3, "defect": 1},
        {"lot": "F251408", "cs": 2, "defect": 0},
        {"lot": "F251413", "cs": 2, "defect": 1},
        {"lot": "F251506", "cs": 2, "defect": 2},
        {"lot": "F251509", "cs": 4, "defect": 0},
        {"lot": "F251510", "cs": 2, "defect": 1},
        {"lot": "F251511", "cs": 2, "defect": 1},
        {"lot": "F251516", "cs": 9, "defect": 6},
        {"lot": "F251520", "cs": 19, "defect": 4},
        {"lot": "F251525", "cs": 19, "defect": 6},
        {"lot": "F251526", "cs": 12, "defect": 5},
        {"lot": "F251608", "cs": 1, "defect": 0},
        {"lot": "F251621", "cs": 1, "defect": 1},
        {"lot": "F251623", "cs": 2, "defect": 1},
        {"lot": "F251701", "cs": 13, "defect": 5},
        {"lot": "F251704", "cs": 2, "defect": 0},
        {"lot": "F251706", "cs": 6, "defect": 0},
        {"lot": "F252007", "cs": 2, "defect": 1},
        {"lot": "F252014", "cs": 5, "defect": 1},
        {"lot": "F252101", "cs": 2, "defect": 2},
        {"lot": "F252106", "cs": 19, "defect": 6},
        {"lot": "F252107", "cs": 1, "defect": 0},
        {"lot": "F252203", "cs": 3, "defect": 1},
        {"lot": "F252204", "cs": 9, "defect": 2},
        {"lot": "F252205", "cs": 3, "defect": 0},
        {"lot": "F252210", "cs": 2, "defect": 2},
        {"lot": "F252218", "cs": 1, "defect": 0},
        {"lot": "F252220", "cs": 1, "defect": 0},
        {"lot": "F252223", "cs": 5, "defect": 2},
        {"lot": "F252304", "cs": 4, "defect": 2},
        {"lot": "F252305", "cs": 2, "defect": 0},
        {"lot": "F252406", "cs": 3, "defect": 1},
        {"lot": "F252407", "cs": 4, "defect": 2},
        {"lot": "F252408", "cs": 1, "defect": 0},
        {"lot": "F252409", "cs": 3, "defect": 1},
        {"lot": "F252410", "cs": 3, "defect": 0},
        {"lot": "F252411", "cs": 3, "defect": 1},
        {"lot": "F252412", "cs": 2, "defect": 0},
        {"lot": "F252413", "cs": 16, "defect": 3},
        {"lot": "F252415", "cs": 7, "defect": 2},
        {"lot": "F252502", "cs": 7, "defect": 4},
        {"lot": "F252503", "cs": 1, "defect": 0},
        {"lot": "F252504", "cs": 2, "defect": 0},
        {"lot": "F252507", "cs": 9, "defect": 4},
        {"lot": "F252509", "cs": 1, "defect": 1},
        {"lot": "F252512", "cs": 6, "defect": 3},
        {"lot": "F252513", "cs": 1, "defect": 0},
        {"lot": "F252516", "cs": 10, "defect": 5},
        {"lot": "F252519", "cs": 4, "defect": 1},
        {"lot": "F252520", "cs": 2, "defect": 1},
        {"lot": "F252521", "cs": 3, "defect": 1},
        {"lot": "F252522", "cs": 4, "defect": 3},
        {"lot": "F252523", "cs": 4, "defect": 1},
        {"lot": "F252524", "cs": 7, "defect": 4},
        {"lot": "F252525", "cs": 2, "defect": 1},
        {"lot": "F252701", "cs": 5, "defect": 2},
        {"lot": "F252704", "cs": 2, "defect": 2},
        {"lot": "F252707", "cs": 10, "defect": 4},
        {"lot": "F252709", "cs": 5, "defect": 2},
        {"lot": "F252804", "cs": 3, "defect": 1},
        {"lot": "F252810", "cs": 1, "defect": 1},
        {"lot": "F252811", "cs": 1, "defect": 0},
        {"lot": "F252812", "cs": 1, "defect": 1},
        {"lot": "F252902", "cs": 1, "defect": 0},
        {"lot": "F252903", "cs": 5, "defect": 2},
        {"lot": "F252907", "cs": 2, "defect": 1},
        {"lot": "F252919", "cs": 1, "defect": 0},
        {"lot": "F253012", "cs": 5, "defect": 0},
        {"lot": "F253020", "cs": 1, "defect": 0},
        {"lot": "F253111", "cs": 6, "defect": 1},
        {"lot": "F253115", "cs": 6, "defect": 3},
        {"lot": "F253205", "cs": 2, "defect": 0},
        {"lot": "F253208", "cs": 2, "defect": 0},
        {"lot": "F253312", "cs": 1, "defect": 0},
        {"lot": "F253314", "cs": 1, "defect": 0},
        {"lot": "F253317", "cs": 1, "defect": 0},
        {"lot": "F253402", "cs": 1, "defect": 1},
        {"lot": "F253407", "cs": 1, "defect": 1},
        {"lot": "F253411", "cs": 1, "defect": 0},
        {"lot": "F253711", "cs": 1, "defect": 1},
        {"lot": "F253712", "cs": 1, "defect": 0},
        {"lot": "F253717", "cs": 2, "defect": 1},
        {"lot": "F253718", "cs": 9, "defect": 2},
        {"lot": "F253720", "cs": 1, "defect": 0},
        {"lot": "F253721", "cs": 1, "defect": 1},
        {"lot": "F253722", "cs": 1, "defect": 1},
        {"lot": "F253723", "cs": 8, "defect": 5},
        {"lot": "F253724", "cs": 2, "defect": 1},
        {"lot": "F253725", "cs": 5, "defect": 2},
        {"lot": "F253729", "cs": 1, "defect": 1},
        {"lot": "F253730", "cs": 5, "defect": 3},
        {"lot": "F253739", "cs": 7, "defect": 0},
        {"lot": "F253741", "cs": 1, "defect": 0},
        {"lot": "F253742", "cs": 1, "defect": 1},
        {"lot": "F253743", "cs": 4, "defect": 0},
        {"lot": "F253802", "cs": 9, "defect": 2},
        {"lot": "F253813", "cs": 1, "defect": 1},
        {"lot": "F253815", "cs": 1, "defect": 0},
        {"lot": "F253816", "cs": 17, "defect": 4},
    ]

    for row in data:
        existing = (
            db.query(LotDefectManual).filter_by(lot=row["lot"], year=2025).first()
        )

        if existing:
            continue

        db.add(
            LotDefectManual(
                lot=row["lot"],
                cs_count=row["cs"],
                defect_count=row["defect"],
                year=2025,
            )
        )

    db.commit()