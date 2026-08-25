import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import business
import config
import db
import profit


class TestProfitCalculations(unittest.TestCase):
    def test_expected_and_actual_profit(self):
        result = profit.calculate_profit({
            "purchase_price": 1000,
            "expected_resale_price": 1800,
            "transport_cost": 100,
            "repair_cost": 50,
            "selling_fee": 50,
            "other_cost": 25,
            "labor_cost": 25,
            "actual_sale_price": 1700,
            "actual_transport_cost": 120,
            "actual_repair_cost": 60,
            "actual_selling_fee": 60,
            "actual_other_cost": 20,
        })
        self.assertEqual(result["total_expected_cost"], 1250)
        self.assertEqual(result["expected_profit"], 550)
        self.assertEqual(result["break_even_price"], 1250)
        self.assertEqual(result["total_actual_cost"], 1285)
        self.assertEqual(result["actual_profit"], 415)

    def test_empty_actual_sale_is_safe(self):
        result = profit.calculate_profit({"purchase_price": 1000})
        self.assertIsNone(result["actual_profit"])
        self.assertIsNone(result["actual_roi_pct"])

    def test_risk_and_score(self):
        result = profit.enrich_listing({
            "title": "Trasig kamera reservdelar",
            "price": 500,
            "expected_resale_price": 1500,
        }, 1500, 12)
        self.assertEqual(result["risk_level"], "high")
        self.assertGreaterEqual(result["deal_score"], 0)
        self.assertLessEqual(result["deal_score"], 100)


class TestBusinessPersistence(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.old_path = config.DB_PATH
        config.DB_PATH = Path(self.temp.name) / "business.db"
        db.init_db()
        business.init_db()
        self.search = db.create_search(0, ["kamera"])
        db.insert_listing(self.search["id"], {
            "ad_id": "abc", "title": "Kamera", "price": 1000,
            "url": "https://example.test/abc",
        })

    def tearDown(self):
        config.DB_PATH = self.old_path
        self.temp.cleanup()

    def test_finance_and_status_persist(self):
        business.save_finance(self.search["id"], "abc", 0, {
            "purchase_price": 1000,
            "expected_resale_price": 1600,
            "transport_cost": 100,
            "category": "Kamera",
        })
        business.set_status(self.search["id"], "abc", 0, "bought")
        item = business.enrich_listing(self.search["id"], db.get_listing(self.search["id"], "abc"))
        self.assertEqual(item["status"], "bought")
        self.assertEqual(item["expected_profit"], 500)
        self.assertEqual(item["category"], "Kamera")

    def test_price_drop_event_is_deduplicated(self):
        business.configure_price_drop(0, self.search["id"], "abc", {
            "enabled": True, "min_drop_amount": 1, "min_drop_pct": 1,
            "last_price": 1000,
        })
        with db.connect() as conn:
            conn.execute(
                "UPDATE listings SET price = ? WHERE search_id = ? AND ad_id = ?",
                (800, self.search["id"], "abc"),
            )
        first = business.check_price_drops(self.search["id"], 0)
        second = business.check_price_drops(self.search["id"], 0)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
