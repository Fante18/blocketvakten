import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import config
import db
import monitor
import notifier


class TestPersistenceAndStatistics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.old_db_path = config.DB_PATH
        config.DB_PATH = Path(self.temp_dir.name) / "test.db"
        db.init_db()

    def tearDown(self):
        config.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_profile_search_email_and_weekly_stats(self):
        search = db.create_search(0, ["kamera"], name="Kameror", send_email=True)
        self.assertTrue(search["send_email"])
        db.set_profile(0, {"email": "person@example.com"})
        self.assertEqual(db.get_profile(0)["email"], "person@example.com")

        for index, age in enumerate((1, 8, 40)):
            ad_id = str(index)
            db.insert_listing(
                search["id"],
                {
                    "ad_id": ad_id,
                    "title": "Kamera",
                    "price": 100 + index * 50,
                    "url": f"https://example.test/{ad_id}",
                },
            )
            seen_at = datetime.now(timezone.utc) - timedelta(days=age)
            with db.connect() as conn:
                conn.execute(
                    "UPDATE listings SET first_seen_at = ? WHERE search_id = ? AND ad_id = ?",
                    (seen_at.isoformat(), search["id"], ad_id),
                )

        stats = db.listing_statistics(search["id"])
        self.assertEqual(stats["avg"], 125.0)
        self.assertEqual(stats["min"], 100)
        self.assertEqual(stats["max"], 150)
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["total_count"], 3)
        self.assertEqual(len(stats["weekly"]), 8)

    @patch("monitor.blocket.fetch_search_listings")
    @patch("monitor.notifier.send_email_for_listings")
    def test_monitor_batches_new_listings_for_one_search(self, send_email, fetch):
        search = db.create_search(0, ["kamera"], name="Kameror", send_email=True)
        db.set_profile(0, {"email": "person@example.com"})
        fetch.return_value = [
            {"ad_id": "1", "title": "Kamera 1", "price": 100, "url": "https://example.test/1", "published_text": ""},
            {"ad_id": "2", "title": "Kamera 2", "price": 200, "url": "https://example.test/2", "published_text": ""},
        ]

        result = monitor.check_search(search)

        self.assertEqual(result["new"], 2)
        send_email.assert_called_once()
        self.assertEqual(len(send_email.call_args.args[1]), 2)
        self.assertEqual(send_email.call_args.kwargs["recipient"], "person@example.com")


class TestBatchedNotifier(unittest.TestCase):
    def test_one_message_contains_all_listing_details(self):
        fake_server = MagicMock()
        fake_server.__enter__.return_value = fake_server
        with patch.object(config, "EMAIL_ENABLED", True), patch.object(
            config, "EMAIL_FROM", "from@example.com"
        ), patch.object(notifier.smtplib, "SMTP", return_value=fake_server):
            sent = notifier.send_email_for_listings(
                "Kameror",
                [
                    {
                        "title": "Kamera 1",
                        "price": 100,
                        "image_url": "https://img.test/1.jpg",
                        "url": "https://blocket.test/1",
                    },
                    {
                        "title": "Kamera 2",
                        "price": 200,
                        "image_url": "",
                        "url": "https://blocket.test/2",
                    },
                ],
                recipient="person@example.com",
            )

        self.assertTrue(sent)
        message = fake_server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "person@example.com")
        content = message.as_string()
        self.assertIn("Kamera 1", content)
        self.assertIn("Kamera 2", content)
        self.assertIn("https://img.test/1.jpg", content)
        self.assertIn("https://blocket.test/2", content)


if __name__ == "__main__":
    unittest.main()
