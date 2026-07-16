import unittest

from tools.gmail_ingest import dedupe_emails


class GmailIngestTest(unittest.TestCase):
    def test_dedupes_overlapping_sender_queries_by_message_id(self):
        first = {
            "message_id": "<same@example.com>",
            "from_address": "swyx+ainews@substack.com",
            "subject": "AI News",
            "published_at": "2026-07-16T00:00:00+00:00",
        }
        duplicate = {**first, "message_id": "<SAME@example.com>"}

        self.assertEqual(dedupe_emails([first, duplicate]), [first])

    def test_dedupes_missing_message_id_by_stable_headers(self):
        first = {
            "message_id": "",
            "from_address": "newsletter@example.com",
            "subject": "Daily brief",
            "published_at": "2026-07-16T00:00:00+00:00",
        }

        self.assertEqual(dedupe_emails([first, dict(first)]), [first])


if __name__ == "__main__":
    unittest.main()
