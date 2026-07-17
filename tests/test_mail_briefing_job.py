import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.install_mail_briefing_schedule import build_plist
from tools.mail_briefing_job import (
    GeneratedItem,
    JobConfig,
    ModelConfig,
    build_batch,
    compact_paragraphs,
    deploy_batch,
    generate_email_items,
    load_config,
    prepare_messages,
    run_job,
    source_id_for,
)
from tools.model_runtime import new_ledger


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
            "refs": [{"paragraph_index": 1, "label": "发布背景", "excerpt_zh": "这家公司发布了一个模型。"}],
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

    def test_deploy_syncs_current_briefing_and_all_linked_pages(self):
        config = load_config(EXAMPLE_CONFIG).deploy
        with tempfile.TemporaryDirectory() as directory:
            batch_dir = Path(directory) / "mail-daily"
            (batch_dir / "audio").mkdir(parents=True)
            (batch_dir / "briefing.json").write_text("{}", encoding="utf-8")
            with patch("tools.mail_briefing_job.subprocess.run") as run:
                url = deploy_batch(batch_dir, config)

        commands = [call.args[0] for call in run.call_args_list]
        flattened = [" ".join(command) for command in commands]
        self.assertEqual(url, f"{config.public_url}/prototype/briefing/index.html")
        self.assertTrue(any("prototype/history/index.html" in command for command in flattened))
        self.assertTrue(any("prototype/relecture/" in command for command in flattened))
        self.assertTrue(any("prototype/generated/" in command for command in flattened))
        self.assertTrue(any("local-data/history/" in command for command in flattened))
        self.assertTrue(any(command.endswith("prototype/briefing/briefing.json") for command in flattened))
        self.assertTrue(any(command.endswith("prototype/briefing/audio/") for command in flattened))

    def test_compact_paragraphs_marks_truncation_without_splitting_paragraph(self):
        paragraphs, truncated = compact_paragraphs(sample_message(), 60)
        self.assertTrue(truncated)
        self.assertEqual(paragraphs[0]["index"], 1)
        self.assertLess(len(paragraphs), 4)

    def test_build_batch_preserves_four_part_attribution_and_source_refs(self):
        message = sample_message()
        drop = {"account": "reader@example.com", "emails": [message]}
        batch = build_batch(drop, [(message, [sample_item()])], ModelConfig())
        self.assertEqual(len(batch.items), 1)
        self.assertEqual(len(batch.items[0].spoken_segments), 4)
        self.assertEqual(batch.items[0].spoken_segments[0].attribution, "source")
        self.assertEqual(batch.items[0].spoken_segments[-1].attribution, "AI")
        self.assertEqual(batch.items[0].spoken_segments[0].source_refs[0].locator, "paragraph-1")
        self.assertEqual(batch.items[0].spoken_segments[0].source_refs[0].excerpt, "The company released a model.")
        self.assertEqual(batch.items[0].spoken_segments[0].source_refs[0].excerpt_zh, "这家公司发布了一个模型。")
        self.assertEqual(batch.sources[0].source_id, source_id_for(message))
        self.assertFalse(hasattr(batch.sources[0], "account"))

    def test_schedule_plist_uses_configured_time_and_private_logs(self):
        value = build_plist(EXAMPLE_CONFIG, ROOT / ".env", Path("/usr/bin/python3"))
        self.assertEqual(value["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertIn("local-data/briefing/logs", value["StandardOutPath"])
        self.assertIn("mail_briefing_job.py", " ".join(value["ProgramArguments"]))

    def test_preflight_budget_stops_before_client_creation_and_writes_failed_usage(self):
        message = sample_message()
        message["paragraphs"][0]["text"] = "x" * 5000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            config["model"]["max_candidate_emails"] = 1
            config["model"]["max_total_input_chars"] = 2000
            config["model"]["max_estimated_input_tokens"] = 100000
            config["model"]["max_estimated_output_tokens"] = 5000
            config["tts"]["enabled"] = False
            config["deploy"]["enabled"] = False
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with (
                patch.dict(os.environ, {
                    "GMAIL_ADDRESS": "reader@example.com",
                    "GMAIL_APP_PASSWORD": "secret",
                    "DEEPSEEK_API_KEY": "secret",
                }),
                patch("tools.mail_briefing_job.DEFAULT_OUTPUT", root / "output"),
                patch("tools.mail_briefing_job.fetch_mail_drop", return_value={"emails": [message]}),
                patch("tools.mail_briefing_job.deepseek_client") as client_factory,
            ):
                with self.assertRaisesRegex(RuntimeError, "未调用模型"):
                    run_job(config_path, root / "missing.env", skip_tts=True)
                client_factory.assert_not_called()
            ledgers = list((root / "output" / "usage").glob("*.json"))
            self.assertEqual(len(ledgers), 1)
            self.assertEqual(json.loads(ledgers[0].read_text(encoding="utf-8"))["status"], "failed")

    def test_retry_usage_is_counted_and_cached_result_uses_zero_calls(self):
        message = sample_message()
        valid = json.dumps({"items": [sample_item().model_dump()]}, ensure_ascii=False)
        responses = [
            types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))],
                usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            ),
            types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=valid))],
                usage={"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
            ),
        ]

        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                response = responses[self.calls]
                self.calls += 1
                return response

        completions = Completions()
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            first = new_ledger("mail", "fixture", "v1")
            items = generate_email_items(
                client,
                message,
                source_id_for(message),
                ModelConfig(model="fixture"),
                cache_root=cache_root,
                ledger=first,
            )
            self.assertEqual(len(items), 1)
            self.assertEqual(completions.calls, 2)
            self.assertEqual(first["totals"]["total_tokens"], 26)
            self.assertEqual([attempt["status"] for attempt in first["attempts"]], ["validation_error", "success"])

            second = new_ledger("mail", "fixture", "v1")
            generate_email_items(
                client,
                message,
                source_id_for(message),
                ModelConfig(model="fixture"),
                cache_root=cache_root,
                ledger=second,
            )
            self.assertEqual(completions.calls, 2)
            self.assertEqual(second["cache_hits"], 1)
            self.assertEqual(second["totals"]["total_tokens"], 0)

    def test_final_validation_failure_keeps_both_attempts_in_failed_run_ledger(self):
        message = sample_message()

        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))],
                    usage={"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
                )

        completions = Completions()
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
            config["model"]["max_candidate_emails"] = 1
            config["model"]["max_model_calls_per_run"] = 1
            config["model"]["max_estimated_output_tokens"] = 5000
            config["tts"]["enabled"] = False
            config["deploy"]["enabled"] = False
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with (
                patch.dict(os.environ, {
                    "GMAIL_ADDRESS": "reader@example.com",
                    "GMAIL_APP_PASSWORD": "secret",
                    "DEEPSEEK_API_KEY": "secret",
                }),
                patch("tools.mail_briefing_job.DEFAULT_OUTPUT", root / "output"),
                patch("tools.mail_briefing_job.fetch_mail_drop", return_value={"emails": [message]}),
                patch("tools.mail_briefing_job.deepseek_client", return_value=client),
            ):
                with self.assertRaisesRegex(RuntimeError, "连续两次"):
                    run_job(config_path, root / "missing.env", skip_tts=True)
            self.assertEqual(completions.calls, 2)
            ledger_path = next((root / "output" / "usage").glob("*.json"))
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertEqual(ledger["status"], "failed")
            self.assertEqual(len(ledger["attempts"]), 2)
            self.assertEqual(ledger["totals"]["total_tokens"], 16)

    def test_prepare_messages_filters_verification_and_duplicate_content(self):
        duplicate = sample_message() | {"message_id": "<other@example.com>"}
        verification = sample_message() | {"subject": "123456 is your verification code"}
        selected, skipped, _ = prepare_messages(
            [sample_message(), duplicate, verification],
            ModelConfig(max_candidate_emails=3),
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual({item["reason"] for item in skipped}, {"duplicate_content", "deterministic_filter"})

    @patch("tools.mail_briefing_job.subprocess.run")
    def test_deploy_batch_syncs_page_and_shared_theme(self, run):
        config = load_config(EXAMPLE_CONFIG).deploy
        batch_dir = ROOT / "local-data" / "briefing" / "sample-batch"

        url = deploy_batch(batch_dir, config)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any(str(ROOT / "prototype" / "index.html") in command for command in commands))
        self.assertTrue(any(str(ROOT / "prototype" / "briefing" / "index.html") in command for command in commands))
        self.assertTrue(any(str(ROOT / "prototype" / "theme.css") in command for command in commands))
        self.assertTrue(url.endswith("/prototype/briefing/index.html"))


if __name__ == "__main__":
    unittest.main()
