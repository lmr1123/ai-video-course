import json
import tempfile
import unittest
from pathlib import Path

from tools.course_pipeline import Course, normalize_captions, write_course


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


if __name__ == "__main__":
    unittest.main()
