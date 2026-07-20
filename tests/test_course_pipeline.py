import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tools.course_pipeline import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MAX_OUTPUT_TOKENS,
    Course,
    generate_course_from_source,
    load_env,
    normalize_captions,
    write_course,
)
from tools.model_runtime import new_ledger


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "course.json"


class CoursePipelineTest(unittest.TestCase):
    def test_glm_is_the_default_course_model(self):
        self.assertEqual(DEFAULT_MODEL, "glm-5.2")
        self.assertEqual(DEFAULT_BASE_URL, "https://open.bigmodel.cn/api/coding/paas/v4")

    def test_load_env_keeps_existing_process_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "ZHIPU_API_KEY=from-file\nZHIPU_BASE_URL=https://example.invalid/v4\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"ZHIPU_API_KEY": "already-set"},
                clear=True,
            ):
                load_env(env_path)
                self.assertEqual(os.environ["ZHIPU_API_KEY"], "already-set")
                self.assertEqual(
                    os.environ["ZHIPU_BASE_URL"],
                    "https://example.invalid/v4",
                )

    def test_normalize_captions_skips_append_events(self):
        data = {"events": [
            {"tStartMs": 0, "dDurationMs": 3000, "segs": [{"utf8": "Hello"}]},
            {"tStartMs": 2500, "dDurationMs": 500, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 3000, "dDurationMs": 3000, "segs": [{"utf8": "world"}]},
        ]}
        self.assertEqual(normalize_captions(data), [{"start": 0, "end": 6, "text": "Hello world"}])

    def test_fixture_validates_and_writes_manifest(self):
        course = Course.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            path = write_course(course, output)
            self.assertTrue(path.exists())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["courses"][0]["video_id"], course.video_id)

    def test_rejects_deep_module_without_matching_topic(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["deep_modules"][0]["start_sec"] = 179
        with self.assertRaises(ValueError):
            Course.model_validate(data)

    def test_same_source_uses_course_cache_and_zero_second_call(self):
        course = Course.model_validate_json(FIXTURE.read_text(encoding="utf-8"))

        class Completions:
            def __init__(self):
                self.calls = 0
                self.requests = []

            def create(self, **kwargs):
                self.calls += 1
                self.requests.append(kwargs)
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content=course.model_dump_json()),
                    )],
                    usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                )

        completions = Completions()
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
        metadata = {"title": course.source_title, "channel": course.channel, "duration": course.duration_sec}
        cues = [{"start": 0, "end": course.duration_sec, "text": "A complete transcript."}]
        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp)
            first = new_ledger("course", "fixture", "v1")
            generate_course_from_source(
                course.source_url,
                "fixture",
                metadata,
                cues,
                client,
                cache_root=cache_root,
                ledger=first,
            )
            second = new_ledger("course", "fixture", "v1")
            generate_course_from_source(
                course.source_url,
                "fixture",
                metadata,
                cues,
                client,
                cache_root=cache_root,
                ledger=second,
            )
            self.assertEqual(completions.calls, 1)
            request = completions.requests[0]
            self.assertEqual(request["model"], "fixture")
            self.assertEqual(request["response_format"], {"type": "json_object"})
            self.assertEqual(request["max_tokens"], MAX_OUTPUT_TOKENS)
            self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})
            self.assertIn('"deep_modules"', request["messages"][1]["content"])
            self.assertEqual(first["totals"]["total_tokens"], 120)
            self.assertEqual(second["cache_hits"], 1)
            self.assertEqual(second["totals"]["total_tokens"], 0)

    def test_invalid_glm_json_is_retried_once(self):
        course = Course.model_validate_json(FIXTURE.read_text(encoding="utf-8"))

        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                content = '{"bad": true}' if self.calls == 1 else course.model_dump_json()
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content=content),
                    )],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )

        completions = Completions()
        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
        metadata = {"title": course.source_title, "channel": course.channel, "duration": course.duration_sec}
        cues = [{"start": 0, "end": course.duration_sec, "text": "A complete transcript."}]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = new_ledger("course", DEFAULT_MODEL, "v1")
            result = generate_course_from_source(
                course.source_url,
                DEFAULT_MODEL,
                metadata,
                cues,
                client,
                cache_root=Path(tmp),
                ledger=ledger,
            )
        self.assertEqual(result.video_id, course.video_id)
        self.assertEqual(completions.calls, 2)
        self.assertEqual([item["status"] for item in ledger["attempts"]], ["validation_error", "success"])


if __name__ == "__main__":
    unittest.main()
