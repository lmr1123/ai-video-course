import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.dubbing_pipeline import (
    DubbingManifest,
    SpeakerProfile,
    Translation,
    build_manifest,
    load_cached_manifest,
    load_speaker_overrides,
    normalize_dubbing_captions,
    parse_time,
    synthesize_audio,
    write_json,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "dubbing.json"


class DubbingPipelineTest(unittest.TestCase):
    def test_parse_time_supports_seconds_and_clock(self):
        self.assertEqual(parse_time("90"), 90)
        self.assertEqual(parse_time("01:30"), 90)
        self.assertEqual(parse_time("01:02:03"), 3723)

    def test_normalize_captions_builds_short_ordered_cues(self):
        data = {"events": [
            {"tStartMs": 100000, "dDurationMs": 2500, "segs": [{"utf8": "First"}]},
            {"tStartMs": 102500, "dDurationMs": 2500, "segs": [{"utf8": "sentence."}]},
            {"tStartMs": 105000, "dDurationMs": 4000, "segs": [{"utf8": "Second sentence."}]},
            {"tStartMs": 106000, "dDurationMs": 500, "aAppend": 1, "segs": [{"utf8": "ignored"}]},
        ]}
        cues = normalize_dubbing_captions(data, 100, 109)
        self.assertEqual([cue["cue_id"] for cue in cues], ["cue-0001", "cue-0002"])
        self.assertEqual(cues[0]["source_text"], "First sentence.")
        self.assertTrue(all(0 < cue["end"] - cue["start"] <= 12 for cue in cues))

    def test_normalize_captions_removes_rolling_window_overlap(self):
        data = {"events": [
            {"tStartMs": 0, "dDurationMs": 6000, "segs": [{"utf8": "First sentence."}]},
            {"tStartMs": 4000, "dDurationMs": 6000, "segs": [{"utf8": "Second sentence."}]},
        ]}
        cues = normalize_dubbing_captions(data, 0, 10)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["end"], cues[1]["start"])

    def test_build_manifest_keeps_source_timestamps(self):
        cues = [{"cue_id": "cue-0001", "start": 10, "end": 16, "source_text": "Hello API."}]
        translations = [Translation(cue_id="cue-0001", zh_spoken="你好，API。", terms=["API"], speaker_id="guest")]
        speakers = [
            SpeakerProfile(speaker_id="host", label="主持人", voice="zh-CN-YunxiNeural", rate="+2%", pitch="-4Hz"),
            SpeakerProfile(speaker_id="guest", label="嘉宾", voice="zh-CN-YunxiNeural", rate="-2%", pitch="-10Hz"),
        ]
        manifest = build_manifest(
            {"duration": 100, "title": "Title", "channel": "Channel"},
            "https://youtu.be/P3KDebPTUrw",
            cues,
            translations,
            speakers,
            "fixture",
        )
        self.assertEqual(manifest.segment_start, 10)
        self.assertEqual(manifest.cues[0].terms, ["API"])
        self.assertEqual(manifest.cues[0].audio_file, "audio/cue-0001.mp3")
        self.assertEqual(manifest.cues[0].speaker_id, "guest")

    def test_fixture_validates_and_writes_local_manifest(self):
        manifest = DubbingManifest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / manifest.video_id / "dubbing.json"
            write_json(path, manifest.model_dump())
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["video_id"], manifest.video_id)

    def test_rejects_overlapping_cues(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["cues"][1]["start"] = 125
        with self.assertRaises(ValueError):
            DubbingManifest.model_validate(data)

    def test_fixture_audio_fits_source_window(self):
        manifest = DubbingManifest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        ratios = [cue.audio_duration / (cue.end - cue.start) for cue in manifest.cues]
        self.assertTrue(all(ratio <= 1.35 for ratio in ratios))

    def test_cache_only_matches_same_cue_timeline_and_text(self):
        manifest = DubbingManifest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        cues = [
            {"cue_id": c.cue_id, "start": c.start, "end": c.end, "source_text": c.source_text}
            for c in manifest.cues
        ]
        self.assertIsNotNone(load_cached_manifest(FIXTURE, cues))
        cues[0]["source_text"] = "Changed source"
        self.assertIsNone(load_cached_manifest(FIXTURE, cues))

    def test_speaker_overrides_validate_ids_and_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "speaker-overrides.json"
            write_json(path, {"cue-0002": "host"})
            self.assertEqual(load_speaker_overrides(path), {"cue-0002": "host"})
            write_json(path, {"cue-2": "narrator"})
            with self.assertRaises(ValueError):
                load_speaker_overrides(path)

    def test_tts_retry_replaces_audio_only_after_success(self):
        manifest = DubbingManifest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
        attempts = 0

        class FakeCommunicate:
            def __init__(self, *args, **kwargs):
                pass

            async def save(self, path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise TimeoutError("temporary failure")
                Path(path).write_bytes(b"new audio")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first_audio = target / manifest.cues[0].audio_file
            first_audio.parent.mkdir(parents=True)
            first_audio.write_bytes(b"old audio")
            with (
                patch.dict(sys.modules, {"edge_tts": types.SimpleNamespace(Communicate=FakeCommunicate)}),
                patch("tools.dubbing_pipeline.audio_duration", return_value=1.0),
                patch("tools.dubbing_pipeline.asyncio.sleep", return_value=None),
            ):
                asyncio.run(synthesize_audio(manifest, target, concurrency=1, force=True))

            self.assertEqual(attempts, len(manifest.cues) + 1)
            self.assertEqual(first_audio.read_bytes(), b"new audio")
            self.assertFalse(first_audio.with_suffix(".tmp.mp3").exists())

    def test_tts_failure_preserves_existing_audio(self):
        manifest = DubbingManifest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))

        class FailingCommunicate:
            def __init__(self, *args, **kwargs):
                pass

            async def save(self, path):
                Path(path).write_bytes(b"partial audio")
                raise TimeoutError("service unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            first_audio = target / manifest.cues[0].audio_file
            first_audio.parent.mkdir(parents=True)
            first_audio.write_bytes(b"old audio")
            with (
                patch.dict(sys.modules, {"edge_tts": types.SimpleNamespace(Communicate=FailingCommunicate)}),
                patch("tools.dubbing_pipeline.asyncio.sleep", return_value=None),
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(synthesize_audio(manifest, target, concurrency=1, force=True))

            self.assertEqual(first_audio.read_bytes(), b"old audio")
            self.assertFalse(first_audio.with_suffix(".tmp.mp3").exists())


if __name__ == "__main__":
    unittest.main()
