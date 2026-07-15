import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.briefing_pipeline import BriefingBatch, safe_filename, synthesize_audio, write_batch


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "briefing.json"


class BriefingPipelineTest(unittest.TestCase):
    def test_fixture_validates_and_writes_batch(self):
        batch = BriefingBatch.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_batch(batch, Path(tmp))
            self.assertTrue(path.exists())
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["batch_id"], "market-scan-demo")
            self.assertEqual(len(written["items"]), 4)

    def test_rejects_duplicate_event_cluster(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["items"][1]["event_cluster_id"] = data["items"][0]["event_cluster_id"]
        with self.assertRaises(ValueError):
            BriefingBatch.model_validate(data)

    def test_rejects_ai_attribution_on_fact(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["items"][0]["spoken_segments"][0]["attribution"] = "AI"
        with self.assertRaises(ValueError):
            BriefingBatch.model_validate(data)

    def test_rejects_unknown_source_reference(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["items"][0]["spoken_segments"][0]["source_refs"][0]["source_id"] = "source-missing"
        with self.assertRaises(ValueError):
            BriefingBatch.model_validate(data)

    def test_rejects_executable_source_link(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["items"][0]["spoken_segments"][0]["source_refs"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            BriefingBatch.model_validate(data)

    def test_safe_audio_filename(self):
        self.assertEqual(safe_filename("item-001", "segment-01"), "item-001-segment-01.mp3")

    def test_tts_cache_is_reused(self):
        batch = BriefingBatch.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        calls = 0

        class FakeCommunicate:
            def __init__(self, *args, **kwargs):
                nonlocal calls
                calls += 1

            async def save(self, path):
                Path(path).write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            with (
                patch.dict(sys.modules, {"edge_tts": types.SimpleNamespace(Communicate=FakeCommunicate)}),
                patch("tools.briefing_pipeline.audio_duration", return_value=1.0),
            ):
                asyncio.run(synthesize_audio(batch, target, "voice", "+0%", "+0Hz"))
                first_calls = calls
                asyncio.run(synthesize_audio(batch, target, "voice", "+0%", "+0Hz"))
            self.assertEqual(calls, first_calls)
            self.assertEqual(first_calls, sum(len(item.spoken_segments) for item in batch.items))


if __name__ == "__main__":
    unittest.main()
