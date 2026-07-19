import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = "eAXxdtNlK04"


class VisualReadingIntegrationTests(unittest.TestCase):
    def test_video_is_a_generated_course_with_visual_reading(self):
        manifest = json.loads((ROOT / "prototype/generated/manifest.json").read_text())
        entries = {item["video_id"]: item for item in manifest["courses"]}
        self.assertIn(VIDEO_ID, entries)
        self.assertTrue(entries[VIDEO_ID]["has_visual_reading"])

        course = json.loads(
            (ROOT / f"prototype/generated/{VIDEO_ID}/course.json").read_text()
        )
        visual = course["learning_views"]["visual_reading"]
        self.assertEqual(visual["label"], "图文精讲")
        self.assertEqual(
            visual["path"], f"/local-data/visual-reading/{VIDEO_ID}/index.html"
        )

    def test_course_and_visual_view_are_bidirectionally_linked(self):
        viewer = (ROOT / "prototype/generated/viewer.html").read_text()
        visual_page = (
            ROOT / f"local-data/visual-reading/{VIDEO_ID}/index.html"
        ).read_text()
        entry = (ROOT / "prototype/index.html").read_text()

        self.assertIn("learning_views?.visual_reading", viewer)
        self.assertIn("当前课程学习方式", viewer)
        self.assertIn('location.hostname.endsWith("github.io")', viewer)
        self.assertIn('body class="page-generated"', viewer)
        self.assertIn('href="../theme.css"', viewer)
        self.assertIn('href="../history/index.html"', viewer)
        self.assertIn(
            f'/prototype/generated/viewer.html?id={VIDEO_ID}', visual_page
        )
        self.assertIn(f"https://youtu.be/{VIDEO_ID}", entry)

    def test_interview_course_does_not_declare_visual_reading(self):
        course = json.loads(
            (ROOT / "prototype/generated/P3KDebPTUrw/course.json").read_text()
        )
        self.assertNotIn("learning_views", course)

    def test_history_falls_back_to_generated_manifest(self):
        history = (ROOT / "prototype/history/index.html").read_text()
        self.assertIn("Promise.allSettled", history)
        self.assertIn('new URL("generated/manifest.json",prototypeRoot)', history)
        self.assertIn('value.slice("prototype/".length)', history)
        self.assertIn("isPublicGithub", history)


if __name__ == "__main__":
    unittest.main()
