import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import QueryParams
from app.database import Base
from app.models.cs import CSCase
from app.utils.query_filters import apply_column_filters


class BlankColumnFilterTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add_all(
            [
                CSCase(product="cart_bp_pro", transfer_no="1", replacement_serial_no=None),
                CSCase(product="cart_bp_pro", transfer_no="2", replacement_serial_no=""),
                CSCase(product="cart_bp_pro", transfer_no="3", replacement_serial_no="G260101-AAAA1"),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_blank_filter_matches_null_and_empty_string(self):
        query = apply_column_filters(
            self.db.query(CSCase),
            QueryParams("cf_교체+S%2FN=__blank__"),
            {"교체 S/N": CSCase.replacement_serial_no},
        )

        self.assertEqual(
            [row.transfer_no for row in query.order_by(CSCase.transfer_no).all()],
            ["1", "2"],
        )


if __name__ == "__main__":
    unittest.main()