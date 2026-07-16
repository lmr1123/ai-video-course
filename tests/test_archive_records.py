import json
import tempfile
import unittest
from pathlib import Path

from tools.archive_records import archive_briefing, archive_course, load_index
from tools.briefing_pipeline import BriefingBatch
from tools.course_pipeline import Course


ROOT = Path(__file__).resolve().parent.parent
COURSE_FIXTURE = ROOT / "tests" / "fixtures" / "course.json"
BRIEFING_FIXTURE = ROOT / "tests" / "fixtures" / "briefing.json"


class ArchiveRecordsTest(unittest.TestCase):
    def test_course_archive_writes_markdown_and_index(self):
        course = Course.model_validate_json(COURSE_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            record = archive_course(course, Path(tmp))
            markdown = Path(record["markdown_path"]).read_text(encoding="utf-8")

            self.assertEqual(record["id"], f"course-{course.video_id}")
            self.assertEqual(record["type"], "course")
            self.assertIn("type: \"video_course\"", markdown)
            self.assertIn("本地历史记录", markdown)
            self.assertIn(course.source_url, markdown)
            self.assertIn("http://localhost:8737/prototype/generated/viewer.html?id=", markdown)

            index = load_index(Path(tmp))
            self.assertEqual(len(index["records"]), 1)
            self.assertEqual(index["records"][0]["web_url"], f"prototype/generated/viewer.html?id={course.video_id}")

    def test_briefing_archive_writes_local_markdown_and_upserts(self):
        data = json.loads(BRIEFING_FIXTURE.read_text(encoding="utf-8"))
        first_ref = data["items"][0]["spoken_segments"][0]["source_refs"][0]
        first_ref["excerpt_zh"] = "Particle 将多源新闻放入一个可连续播放的音频队列。"
        first_ref["excerpt"] = "Particle puts multi-source news into an audio queue."
        batch = BriefingBatch.model_validate(data)
        first_source = data["sources"][0]
        mail_drop = {
            "emails": [
                {
                    "gmail_url": first_source["url"],
                    "subject": first_source["title"],
                    "from_name": first_source.get("author", ""),
                    "paragraphs": [
                        {"index": 1, "text_zh": "这是第一段完整邮件中文内容。"},
                        {
                            "index": 2,
                            "text": "@lvwerra [ https://substack.com/redirect/abc?j=token ] and @thealexker [ https://substack.com/redirect/def?j=token ] discussed the release.",
                        },
                        {"index": 3, "text": "View this post on the web at https://example.com/post"},
                        {"index": 4, "text": "Unsubscribe"},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            first = archive_briefing(batch, Path(tmp), mail_drop=mail_drop)
            second = archive_briefing(batch, Path(tmp), mail_drop=mail_drop)

            self.assertEqual(first["id"], second["id"])
            self.assertNotIn("obsidian_path", second)
            self.assertTrue(Path(second["markdown_path"]).exists())
            markdown = Path(second["markdown_path"]).read_text(encoding="utf-8")
            self.assertIn("type: \"briefing_batch\"", markdown)
            self.assertIn("资讯速听生成记录", markdown)
            self.assertIn("http://localhost:8737/prototype/briefing/?batch=", markdown)
            self.assertIn("来源内容：", markdown)
            self.assertIn("Particle 将多源新闻放入一个可连续播放的音频队列。", markdown)
            self.assertIn("英文原文：", markdown)
            self.assertIn("Particle puts multi-source news into an audio queue.", markdown)
            self.assertIn("见下方完整邮件内容，可按段落编号核对。", markdown)
            self.assertNotIn("对应速听摘要", markdown)
            self.assertIn("完整邮件内容（原文，尚未翻译）：", markdown)
            self.assertIn("[1] 这是第一段完整邮件中文内容。", markdown)
            self.assertIn("[2] @lvwerra and @thealexker discussed the release.", markdown)
            self.assertNotIn("substack.com/redirect", markdown)
            self.assertNotIn("Unsubscribe", markdown)
            self.assertNotIn("View this post on the web", markdown)

            index = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["records"]), 1)
            self.assertEqual(index["records"][0]["type"], "briefing")

    def test_briefing_archive_marks_missing_source_when_no_mail_drop(self):
        batch = BriefingBatch.model_validate_json(BRIEFING_FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            record = archive_briefing(batch, Path(tmp))
            markdown = Path(record["markdown_path"]).read_text(encoding="utf-8")
            self.assertIn("当前内容包没有保存这段来源摘录。", markdown)

if __name__ == "__main__":
    unittest.main()
