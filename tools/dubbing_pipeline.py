#!/usr/bin/env python3
"""为 YouTube 视频片段生成本地中文翻译音轨。

示例：
  OPENAI_API_KEY=... python3 tools/dubbing_pipeline.py \
    https://youtu.be/VIDEO_ID --start 00:10:00 --minutes 15 --serve

产物只写入 local-data/<video-id>/，该目录不会被 Git 跟踪。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

try:
    from tools.course_pipeline import extract_video_id, run
    from tools.model_runtime import (
        add_attempt,
        estimate_tokens_from_chars,
        finish_ledger,
        new_ledger,
        stable_hash,
        write_json_atomic,
    )
except ModuleNotFoundError:  # 直接运行 python3 tools/dubbing_pipeline.py
    from course_pipeline import extract_video_id, run
    from model_runtime import (
        add_attempt,
        estimate_tokens_from_chars,
        finish_ledger,
        new_ledger,
        stable_hash,
        write_json_atomic,
    )


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "local-data"
DEFAULT_MODEL_CACHE = ROOT / "local-data" / "model-cache" / "dubbing"
DEFAULT_USAGE = ROOT / "local-data" / "usage" / "dubbing"
DUBBING_PROMPT_VERSION = "dubbing-v1"


class Translation(BaseModel):
    cue_id: str
    zh_spoken: str = Field(min_length=1)
    terms: list[str] = Field(default_factory=list)
    speaker_id: Literal["host", "guest"]


class TranslationBatch(BaseModel):
    translations: list[Translation]


class DubbingCue(BaseModel):
    cue_id: str = Field(pattern=r"^cue-\d{4}$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    source_text: str = Field(min_length=1)
    zh_spoken: str = Field(min_length=1)
    terms: list[str] = Field(default_factory=list)
    speaker_id: Literal["host", "guest"]
    audio_file: str
    audio_duration: float | None = Field(default=None, gt=0)
    status: str = "translated"

    @model_validator(mode="after")
    def validate_range(self):
        if self.end <= self.start:
            raise ValueError(f"字幕时间范围无效：{self.cue_id}")
        return self


class SpeakerProfile(BaseModel):
    speaker_id: Literal["host", "guest"]
    label: str
    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"


class DubbingManifest(BaseModel):
    version: int = 1
    video_id: str = Field(pattern=r"^[\w-]{11}$")
    source_url: str
    source_title: str
    channel: str = ""
    video_duration: float = Field(gt=0)
    segment_start: float = Field(ge=0)
    segment_end: float = Field(gt=0)
    speakers: list[SpeakerProfile] = Field(min_length=2, max_length=2)
    model: str
    prompt_version: str = ""
    cues: list[DubbingCue] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.segment_end <= self.segment_start:
            raise ValueError("生成片段结束时间必须晚于开始时间")
        speaker_ids = {speaker.speaker_id for speaker in self.speakers}
        if speaker_ids != {"host", "guest"}:
            raise ValueError("speakers 必须同时定义 host 和 guest")
        previous_end = -1.0
        for cue in self.cues:
            if cue.start < previous_end - 0.05:
                raise ValueError(f"字幕时间重叠：{cue.cue_id}")
            if cue.start < self.segment_start - 0.1 or cue.end > self.segment_end + 0.1:
                raise ValueError(f"字幕超出生成片段：{cue.cue_id}")
            if cue.speaker_id not in speaker_ids:
                raise ValueError(f"字幕角色未定义：{cue.cue_id}")
            previous_end = cue.end
        return self


def parse_time(value: str) -> float:
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    parts = value.split(":")
    if len(parts) not in (2, 3) or any(not re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
        raise argparse.ArgumentTypeError("时间应为秒数、MM:SS 或 HH:MM:SS")
    numbers = [float(p) for p in parts]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def fetch_caption_json(url: str) -> tuple[dict, dict]:
    if not shutil.which("yt-dlp"):
        raise RuntimeError("缺少 yt-dlp，请先安装：brew install yt-dlp")
    metadata = json.loads(run(["yt-dlp", "--no-update", "--dump-single-json", "--skip-download", url]))
    available = {**metadata.get("automatic_captions", {}), **metadata.get("subtitles", {})}
    language = "en-orig" if "en-orig" in available else "en" if "en" in available else None
    if language is None:
        raise RuntimeError("视频没有可用的英文字幕")
    with tempfile.TemporaryDirectory() as tmp:
        output = str(Path(tmp) / "%(id)s.%(ext)s")
        run([
            "yt-dlp", "--no-update", "--write-subs", "--write-auto-subs",
            "--sub-langs", language, "--sub-format", "json3", "--skip-download",
            "-o", output, url,
        ])
        files = list(Path(tmp).glob("*.json3"))
        if not files:
            raise RuntimeError("视频没有可用的英文字幕")
        captions = json.loads(files[0].read_text(encoding="utf-8"))
    return metadata, captions


def _raw_events(data: dict) -> list[dict]:
    events = []
    for event in data.get("events", []):
        if event.get("aAppend") or not event.get("segs"):
            continue
        text = "".join(seg.get("utf8", "") for seg in event["segs"])
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000
        end = (event.get("tStartMs", 0) + event.get("dDurationMs", 0)) / 1000
        events.append({"start": round(start, 3), "end": round(max(start + 0.2, end), 3), "text": text})
    return events


def normalize_dubbing_captions(
    data: dict, requested_start: float, requested_end: float, max_span: float = 12
) -> list[dict]:
    """把 json3 字幕整理为适合 TTS 的短语义句。"""
    selected = [e for e in _raw_events(data) if e["end"] > requested_start and e["start"] < requested_end]
    if not selected:
        return []

    groups: list[dict] = []
    sentence_end = re.compile(r"[.!?。！？][\"']?$")
    for event in selected:
        start = max(requested_start, event["start"])
        end = min(requested_end, event["end"])
        if end <= start:
            continue
        event = {"start": start, "end": end, "text": event["text"]}
        if not groups:
            groups.append(event)
            continue
        current = groups[-1]
        combined_span = event["end"] - current["start"]
        gap = event["start"] - current["end"]
        should_merge = (
            gap <= 1.2
            and combined_span <= max_span
            and ((current["end"] - current["start"] < 3) or not sentence_end.search(current["text"]))
        )
        if should_merge:
            current["end"] = event["end"]
            current["text"] += " " + event["text"]
        else:
            groups.append(event)

    # 极短尾句优先并入前句，避免 TTS 碎裂。
    if len(groups) > 1 and groups[-1]["end"] - groups[-1]["start"] < 3:
        previous, last = groups[-2], groups[-1]
        if last["end"] - previous["start"] <= max_span:
            previous["end"] = last["end"]
            previous["text"] += " " + last["text"]
            groups.pop()

    # YouTube 自动字幕使用滚动窗口，相邻事件常重叠 1—3 秒。以重叠区中点
    # 作为唯一边界，避免两个中文音频同时争用一段原片时间。
    for current, following in zip(groups, groups[1:]):
        if following["start"] < current["end"]:
            boundary = (following["start"] + current["end"]) / 2
            current["end"] = boundary
            following["start"] = boundary

    # 从相邻长句借少量时间，尽量保证每条至少 3 秒；不移动片段总边界。
    for index, current in enumerate(groups):
        duration = current["end"] - current["start"]
        if duration >= 3:
            continue
        missing = 3 - duration
        if index + 1 < len(groups):
            following = groups[index + 1]
            available = following["end"] - following["start"] - 3
            shift = min(missing, max(0, available))
            current["end"] += shift
            following["start"] += shift
            missing -= shift
        if missing > 0 and index > 0:
            previous = groups[index - 1]
            available = previous["end"] - previous["start"] - 3
            shift = min(missing, max(0, available))
            current["start"] -= shift
            previous["end"] -= shift

    return [
        {
            "cue_id": f"cue-{index:04d}",
            "start": round(cue["start"], 3),
            "end": round(cue["end"], 3),
            "source_text": cue["text"],
        }
        for index, cue in enumerate(groups, 1)
    ]


def translate_cues(
    cues: list[dict],
    model: str,
    source_title: str = "",
    batch_size: int = 35,
    *,
    cache_root: Path = DEFAULT_MODEL_CACHE,
    ledger: dict | None = None,
    client=None,
) -> list[Translation]:
    active_ledger = ledger if ledger is not None else new_ledger("dubbing", model, DUBBING_PROMPT_VERSION)
    output: list[Translation] = []
    instructions = """你是技术视频中文配音译者。把每条英文字幕翻译成自然、紧凑、适合朗读的简体中文。
要求：
1. 只翻译作者说的内容，不补充解释或评价；
2. 保留否定、条件、数字、比较对象和不确定语气；
3. 框架名、API、模型名、论文名和代码符号保留原文；
4. 中文应尽量能在原时间段内说完，避免书面腔；
5. cue_id 必须原样返回且每条恰好返回一次；
6. terms 只列需要保留原文或注意发音的技术专名；
7. 这是访谈，speaker_id 只能是 host 或 guest。主持人通常提问、串场、读赞助；嘉宾通常回答。结合相邻句保持角色连续，不要按句子长短猜测。"""
    for offset in range(0, len(cues), batch_size):
        batch = cues[offset:offset + batch_size]
        payload = {
            "source_title": source_title,
            "cues": [{"cue_id": c["cue_id"], "seconds": round(c["end"] - c["start"], 1), "text": c["source_text"]} for c in batch],
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        cache_key = stable_hash({
            "prompt_version": DUBBING_PROMPT_VERSION,
            "model": model,
            "instructions": instructions,
            "payload": payload,
        })
        cache_path = cache_root / f"{cache_key}.json"
        response = None
        if cache_path.exists():
            parsed = TranslationBatch.model_validate_json(cache_path.read_text(encoding="utf-8"))
            active_ledger["cache_hits"] += 1
        else:
            active_ledger["cache_misses"] += 1
            if client is None:
                try:
                    from openai import OpenAI
                except ImportError as exc:
                    raise RuntimeError("缺少 openai SDK，请运行：pip install -r requirements.txt") from exc
                if not os.getenv("OPENAI_API_KEY"):
                    raise RuntimeError("缺少 OPENAI_API_KEY；密钥只应设置在本地环境变量")
                client = OpenAI()
            try:
                response = client.responses.parse(
                    model=model,
                    instructions=instructions,
                    input=serialized,
                    text_format=TranslationBatch,
                    max_output_tokens=8000,
                )
            except Exception as exc:
                add_attempt(
                    active_ledger,
                    unit=f"cues-{offset + 1}-{offset + len(batch)}",
                    attempt=1,
                    status="request_error",
                    error=str(exc),
                )
                raise
            parsed = response.output_parsed
            if parsed is None:
                add_attempt(
                    active_ledger,
                    unit=f"cues-{offset + 1}-{offset + len(batch)}",
                    attempt=1,
                    status="parse_error",
                    usage=getattr(response, "usage", None),
                    error="模型没有返回可解析的中文配音稿",
                )
                raise RuntimeError("模型没有返回可解析的中文配音稿")
        active_ledger["estimated"].setdefault("input_chars", 0)
        active_ledger["estimated"].setdefault("input_tokens", 0)
        active_ledger["estimated"].setdefault("output_tokens", 0)
        active_ledger["estimated"]["input_chars"] += len(instructions) + len(serialized)
        active_ledger["estimated"]["input_tokens"] += estimate_tokens_from_chars(len(instructions) + len(serialized))
        active_ledger["estimated"]["output_tokens"] += 8000
        expected = [c["cue_id"] for c in batch]
        received = [item.cue_id for item in parsed.translations]
        if received != expected:
            if response is not None:
                add_attempt(
                    active_ledger,
                    unit=f"cues-{offset + 1}-{offset + len(batch)}",
                    attempt=1,
                    status="validation_error",
                    usage=getattr(response, "usage", None),
                    error=f"翻译 cue_id 不匹配：期望 {expected[0]}…，实际 {received[:3]}",
                )
            else:
                cache_path.unlink(missing_ok=True)
            raise RuntimeError(f"翻译 cue_id 不匹配：期望 {expected[0]}…，实际 {received[:3]}")
        if response is not None:
            add_attempt(
                active_ledger,
                unit=f"cues-{offset + 1}-{offset + len(batch)}",
                attempt=1,
                status="success",
                usage=getattr(response, "usage", None),
            )
            write_json_atomic(cache_path, parsed.model_dump())
        output.extend(parsed.translations)
    return output


def build_manifest(
    metadata: dict,
    url: str,
    cues: list[dict],
    translations: list[Translation],
    speakers: list[SpeakerProfile],
    model: str,
) -> DubbingManifest:
    translated = {item.cue_id: item for item in translations}
    items = []
    for cue in cues:
        item = translated.get(cue["cue_id"])
        if item is None:
            raise ValueError(f"缺少翻译：{cue['cue_id']}")
        items.append(DubbingCue(
            **cue,
            zh_spoken=item.zh_spoken,
            terms=item.terms,
            speaker_id=item.speaker_id,
            audio_file=f"audio/{cue['cue_id']}.mp3",
        ))
    duration = float(metadata.get("duration") or items[-1].end)
    return DubbingManifest(
        video_id=extract_video_id(url),
        source_url=url,
        source_title=metadata.get("title", ""),
        channel=metadata.get("channel") or metadata.get("uploader") or "",
        video_duration=duration,
        segment_start=items[0].start,
        segment_end=items[-1].end,
        speakers=speakers,
        model=model,
        prompt_version=DUBBING_PROMPT_VERSION,
        cues=items,
    )


def load_speaker_overrides(path: Path) -> dict[str, Literal["host", "guest"]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("speaker-overrides.json 必须是 cue_id 到 host/guest 的对象")
    for cue_id, speaker_id in data.items():
        if not re.fullmatch(r"cue-\d{4}", cue_id) or speaker_id not in {"host", "guest"}:
            raise ValueError(f"无效的说话人覆盖：{cue_id}={speaker_id}")
    return data


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audio_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        return None
    return round(float(result.stdout.strip()), 3)


async def synthesize_audio(
    manifest: DubbingManifest, target: Path, concurrency: int = 4, force: bool = False
) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("缺少 edge-tts，请运行：pip install -r requirements.txt") from exc
    semaphore = asyncio.Semaphore(concurrency)
    audio_dir = target / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    profiles = {speaker.speaker_id: speaker for speaker in manifest.speakers}

    async def synthesize(cue: DubbingCue) -> None:
        path = target / cue.audio_file
        if force or not path.exists() or path.stat().st_size == 0:
            async with semaphore:
                profile = profiles[cue.speaker_id]
                temporary = path.with_suffix(".tmp.mp3")
                try:
                    for attempt in range(3):
                        try:
                            temporary.unlink(missing_ok=True)
                            await edge_tts.Communicate(
                                cue.zh_spoken, profile.voice, rate=profile.rate, pitch=profile.pitch
                            ).save(str(temporary))
                            if not temporary.exists() or temporary.stat().st_size == 0:
                                raise RuntimeError("TTS 返回了空音频")
                            temporary.replace(path)
                            break
                        except Exception as exc:
                            if attempt == 2:
                                raise RuntimeError(f"{cue.cue_id} 音频生成失败") from exc
                            await asyncio.sleep(1.5 * (attempt + 1))
                finally:
                    temporary.unlink(missing_ok=True)
        cue.audio_duration = audio_duration(path)
        source_duration = cue.end - cue.start
        cue.status = "too_long" if cue.audio_duration and cue.audio_duration / source_duration > 1.35 else "ready"

    await asyncio.gather(*(synthesize(cue) for cue in manifest.cues))


def load_cached_manifest(path: Path, cues: list[dict]) -> DubbingManifest | None:
    if not path.exists():
        return None
    try:
        existing = DubbingManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    source = [(c.cue_id, c.start, c.end, c.source_text) for c in existing.cues]
    current = [(c["cue_id"], c["start"], c["end"], c["source_text"]) for c in cues]
    if source != current:
        return None
    return existing


def generate(args: argparse.Namespace, ledger: dict | None = None) -> tuple[DubbingManifest, Path]:
    metadata, caption_json = fetch_caption_json(args.url)
    video_id = extract_video_id(args.url)
    video_duration = float(metadata.get("duration") or math.inf)
    requested_end = min(args.start + args.minutes * 60, video_duration)
    cues = normalize_dubbing_captions(caption_json, args.start, requested_end)
    if not cues:
        raise RuntimeError("所选时间范围内没有英文字幕")
    target = args.output_root / video_id
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "dubbing.json"
    cached = load_cached_manifest(manifest_path, cues)
    if cached is not None and (cached.model != args.model or cached.prompt_version != DUBBING_PROMPT_VERSION):
        cached = None
    translations = None if cached is None else [
        Translation(cue_id=c.cue_id, zh_spoken=c.zh_spoken, terms=c.terms, speaker_id=c.speaker_id)
        for c in cached.cues
    ]
    if translations is None:
        translations = translate_cues(
            cues,
            args.model,
            metadata.get("title", ""),
            cache_root=args.model_cache_root,
            ledger=ledger,
        )
    elif ledger is not None:
        ledger["cache_hits"] += 1
    overrides_path = target / "speaker-overrides.json"
    if not overrides_path.exists():
        write_json(overrides_path, {})
    overrides = load_speaker_overrides(overrides_path)
    for translation in translations:
        if translation.cue_id in overrides:
            translation.speaker_id = overrides[translation.cue_id]
    speakers = [
        SpeakerProfile(
            speaker_id="host", label="主持人", voice=args.host_voice,
            rate=args.host_rate, pitch=args.host_pitch,
        ),
        SpeakerProfile(
            speaker_id="guest", label="嘉宾", voice=args.guest_voice,
            rate=args.guest_rate, pitch=args.guest_pitch,
        ),
    ]
    manifest = build_manifest(metadata, args.url, cues, translations, speakers, args.model)
    write_json(target / "source.json", {
        "video_id": video_id,
        "source_url": args.url,
        "source_title": metadata.get("title", ""),
        "channel": metadata.get("channel") or metadata.get("uploader") or "",
        "video_duration": metadata.get("duration"),
        "requested_start": args.start,
        "requested_minutes": args.minutes,
    })
    write_json(target / "captions.en.json", cues)
    write_json(manifest_path, manifest.model_dump())
    if not args.skip_tts:
        force_audio = cached is None or cached.speakers != manifest.speakers or any(
            before.speaker_id != after.speaker_id for before, after in zip(cached.cues, manifest.cues)
        )
        asyncio.run(synthesize_audio(manifest, target, args.concurrency, force=force_audio))
        write_json(manifest_path, manifest.model_dump())
        too_long = sum(cue.status == "too_long" for cue in manifest.cues)
        if too_long:
            print(f"警告：{too_long}/{len(manifest.cues)} 条中文音频超过 1.35× 同步上限，请压缩译文", file=sys.stderr)
    return manifest, manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="生成本地原片同步中文音轨")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--start", type=parse_time, default=0, help="开始时间：秒、MM:SS 或 HH:MM:SS")
    parser.add_argument("--minutes", type=float, default=15, help="生成时长，首次验证建议 10—20 分钟")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-5.4"))
    parser.add_argument("--host-voice", default="zh-CN-YunxiNeural")
    parser.add_argument("--host-pitch", default="-4Hz")
    parser.add_argument("--host-rate", default="+2%")
    parser.add_argument("--guest-voice", default="zh-CN-YunxiNeural")
    parser.add_argument("--guest-pitch", default="-10Hz")
    parser.add_argument("--guest-rate", default="-2%")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-cache-root", type=Path, default=DEFAULT_MODEL_CACHE)
    parser.add_argument("--usage-root", type=Path, default=DEFAULT_USAGE)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--skip-tts", action="store_true", help="只生成字幕和中文口语稿")
    parser.add_argument("--fixture", type=Path, help="离线校验并写入已有 dubbing manifest")
    parser.add_argument("--serve", action="store_true", help="生成后启动本地播放器")
    parser.add_argument("--port", type=int, default=8737)
    args = parser.parse_args()
    if not 10 <= args.minutes <= 20 and not args.fixture:
        parser.error("首次验证的 --minutes 必须在 10 到 20 之间")
    if args.fixture:
        manifest = DubbingManifest.model_validate_json(args.fixture.read_text(encoding="utf-8"))
        target = args.output_root / manifest.video_id
        write_json(target / "dubbing.json", manifest.model_dump())
        path = target / "dubbing.json"
    elif args.url:
        ledger = new_ledger("dubbing", args.model, DUBBING_PROMPT_VERSION)
        usage_path = args.usage_root / f"{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.json"
        try:
            manifest, path = generate(args, ledger)
            finish_ledger(ledger, "success")
            write_json_atomic(usage_path, ledger)
        except Exception as exc:
            finish_ledger(ledger, "failed", str(exc))
            write_json_atomic(usage_path, ledger)
            raise
    else:
        parser.error("请提供 YouTube URL 或 --fixture")
    print(f"中文音轨已生成：{path}")
    print(f"本地播放：http://localhost:{args.port}/prototype/local-player/?id={manifest.video_id}")
    if args.serve:
        try:
            from tools.serve_local import serve
        except ModuleNotFoundError:
            from serve_local import serve
        serve(args.port, manifest.video_id)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
