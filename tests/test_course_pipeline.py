import json
import tempfile
import types
import unittest
from pathlib import Path

from tools.course_pipeline import Course, generate_course_from_source, normalize_captions, write_course
from tools.model_runtime import new_ledger


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "course.json"


class CoursePipelineTest(unittest.TestCase):
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

        class Responses:
            def __init__(self):
                self.calls = 0

            def parse(self, **kwargs):
                self.calls += 1
                return types.SimpleNamespace(
                    output_parsed=course.model_copy(deep=True),
                    usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                )

        responses = Responses()
        client = types.SimpleNamespace(responses=responses)
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
            self.assertEqual(responses.calls, 1)
            self.assertEqual(first["totals"]["total_tokens"], 120)
            self.assertEqual(second["cache_hits"], 1)
            self.assertEqual(second["totals"]["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
