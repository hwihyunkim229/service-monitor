import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.models.cs import CSCase


class CSCaseAutofillTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_blank_hospital_and_size_are_copied_from_same_serial(self):
        self.db.add(
            CSCase(
                product="cart_bp_pro",
                transfer_no="source",
                serial_no="F250101-AAAA1",
                hospital_name="테스트병원",
                size="9호",
            )
        )
        self.db.commit()

        target = CSCase(
            product="cart_bp_pro",
            transfer_no="target",
            serial_no="f250101-aaaa1",
            hospital_name="",
            size=None,
        )
        self.db.add(target)
        self.db.commit()

        self.assertEqual(target.hospital_name, "테스트병원")
        self.assertEqual(target.size, "9")

    def test_existing_values_are_not_overwritten(self):
        self.db.add(
            CSCase(
                product="cart_bp_pro",
                transfer_no="source",
                serial_no="F250101-BBBB1",
                hospital_name="기존병원",
                size="9",
            )
        )
        self.db.commit()
        target = CSCase(
            product="cart_bp_pro",
            transfer_no="target",
            serial_no="F250101-BBBB1",
            hospital_name="입력병원",
            size="10",
        )
        self.db.add(target)
        self.db.commit()

        self.assertEqual(target.hospital_name, "입력병원")
        self.assertEqual(target.size, "10")

    def test_conflicting_hospitals_are_not_chosen(self):
        self.db.add_all(
            [
                CSCase(
                    product="cart_bp_pro",
                    transfer_no="one",
                    serial_no="F250101-CCCC1",
                    hospital_name="가병원",
                    size="8",
                ),
                CSCase(
                    product="cart_bp_pro",
                    transfer_no="two",
                    serial_no="F250101-CCCC1",
                    hospital_name="나병원",
                    size="8호",
                ),
            ]
        )
        self.db.commit()
        target = CSCase(
            product="cart_bp_pro",
            transfer_no="target",
            serial_no="F250101-CCCC1",
        )
        self.db.add(target)
        self.db.commit()

        self.assertIsNone(target.hospital_name)
        self.assertEqual(target.size, "8")

    def test_existing_blank_row_is_filled_when_serial_is_updated(self):
        self.db.add(
            CSCase(
                product="cart_bp_pro",
                transfer_no="source",
                serial_no="F250101-DDDD1",
                hospital_name="업데이트병원",
                size="11",
            )
        )
        target = CSCase(product="cart_bp_pro", transfer_no="target")
        self.db.add(target)
        self.db.commit()

        target.serial_no = "F250101-DDDD1"
        self.db.commit()

        self.assertEqual(target.hospital_name, "업데이트병원")
        self.assertEqual(target.size, "11")


if __name__ == "__main__":
    unittest.main()