import unittest
from pathlib import Path

from tools.install_mail_briefing_schedule import build_plist
from tools.mail_briefing_job import (
    GeneratedItem,
    JobConfig,
    ModelConfig,
    build_batch,
    compact_paragraphs,
    load_config,
    source_id_for,
)


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = ROOT / "config" / "mail-briefing.example.json"


def sample_message() -> dict:
    return {
        "message_id": "<sample@example.com>",
        "from_name": "AI Newsletter",
        "from_address": "news@example.com",
        "subject": "A model release",
        "published_at": "2026-07-16T08:00:00+00:00",
        "extraction_status": "complete",
        "gmail_url": "https://mail.google.com/mail/u/0/#search/sample",
        "paragraphs": [
            {"index": 1, "text": "The company released a model."},
            {"index": 2, "text": "The model uses a mixture of experts."},
            {"index": 3, "text": "The release is open weight."},
            {"index": 4, "text": "Benchmarks have limitations."},
        ],
    }


def sample_item() -> GeneratedItem:
    source_segments = [
        {
            "text": "这是一家模型公司发布的新模型。",
            "kind": "fact",
            "attribution": "source",
            "refs": [{"paragraph_index": 1, "label": "发布背景"}],
        },
        {
            "text": "模型采用混合专家结构。",
            "kind": "fact",
            "attribution": "source",
            "refs": [{"paragraph_index": 2, "label": "模型结构"}],
        },
        {
            "text": "通俗理解是按任务激活部分能力。",
            "kind": "inference",
            "attribution": "AI",
            "refs": [{"paragraph_index": 2, "label": "原理解释"}],
        },
        {
            "text": "它反映开放模型更重视部署，但跑分仍有限定。",
            "kind": "inference",
            "attribution": "AI",
            "refs": [{"paragraph_index": 4, "label": "能力边界"}],
        },
    ]
    return GeneratedItem.model_validate(
        {
            "title": "开放模型开始强调可部署性",
            "content_type": "model",
            "event_cluster_id": "open-model-deployability",
            "essence": "新模型同时强调开放权重和部署生态。",
            "priority": 5,
            "spoken_segments": source_segments,
            "why_it_matters": "开放模型竞争进入工程阶段。",
            "caveats": ["跑分不等于真实任务。"],
            "suggested_action": "deepen",
            "action_reason": "值得用真实任务测试。",
        }
    )


class MailBriefingJobTest(unittest.TestCase):
    def test_example_config_is_valid_and_uses_current_deepseek_model(self):
        config = load_config(EXAMPLE_CONFIG)
        self.assertEqual(config.model.model, "deepseek-v4-flash")
        self.assertEqual(config.schedule.hour, 8)
        self.assertTrue(config.deploy.public_url.startswith("https://"))

    def test_compact_paragraphs_marks_truncation_without_splitting_paragraph(self):
        paragraphs, truncated = compact_paragraphs(sample_message(), 60)
        self.assertTrue(truncated)
        self.assertEqual(paragraphs[0]["index"], 1)
        self.assertLess(len(paragraphs), 4)

    def test_build_batch_preserves_four_part_attribution_and_source_refs(self):
        message = sample_message()
        drop = {"emails": [message]}
        batch = build_batch(drop, [(message, [sample_item()])], ModelConfig())
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(len(batch.items[0].spoken_segments), 4)
        self.assertEqual(batch.items[0].spoken_segments[0].attribution, "source")
        self.assertEqual(batch.items[0].spoken_segments[-1].attribution, "AI")
        self.assertEqual(batch.items[0].spoken_segments[0].source_refs[0].locator, "paragraph-1")
        self.assertEqual(batch.sources[0].source_id, source_id_for(message))

    def test_schedule_plist_uses_configured_time_and_private_logs(self):
        value = build_plist(EXAMPLE_CONFIG, ROOT / ".env", Path("/usr/bin/python3"))
        self.assertEqual(value["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertIn("local-data/briefing/logs", value["StandardOutPath"])
        self.assertIn("mail_briefing_job.py", " ".join(value["ProgramArguments"]))


if __name__ == "__main__":
    unittest.main()
