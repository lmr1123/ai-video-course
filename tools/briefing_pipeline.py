#!/usr/bin/env python3
"""校验多源资讯速听内容包，并可选生成逐句本地语音。

离线验证：
  python3 tools/briefing_pipeline.py \
    --fixture tests/fixtures/briefing.json \
    --output-root /tmp/briefing-output

本地语音与播放器：
  python3 tools/briefing_pipeline.py \
    --fixture tests/fixtures/briefing.json --tts --serve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "local-data" / "briefing"


def validate_link(value: str) -> str:
    if re.match(r"^https?://", value) or (value.startswith("/") and not value.startswith("//")):
        return value
    raise ValueError("来源链接只允许 http、https 或站内绝对路径")


class SourceRef(BaseModel):
    source_id: str = Field(pattern=r"^source-[\w-]+$")
    label: str = Field(min_length=1)
    anchor_kind: Literal["paragraph", "page", "timestamp", "image_region", "email_paragraph"]
    locator: str = Field(min_length=1)
    url: str = Field(min_length=1)

    _validate_url = field_validator("url")(validate_link)


class BriefingSource(BaseModel):
    source_id: str = Field(pattern=r"^source-[\w-]+$")
    kind: Literal["paste", "web", "x", "email", "image", "pdf", "podcast", "youtube", "audio", "video"]
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    author: str = ""
    published_at: str = ""
    extraction_status: Literal["complete", "partial"] = "complete"

    _validate_url = field_validator("url")(validate_link)


class SpokenSegment(BaseModel):
    segment_id: str = Field(pattern=r"^segment-\d{2}$")
    text: str = Field(min_length=1)
    kind: Literal["fact", "opinion", "inference"]
    attribution: Literal["source", "AI"]
    source_refs: list[SourceRef] = Field(min_length=1)
    audio_file: str | None = None
    audio_duration: float | None = Field(default=None, gt=0)


class BriefingItem(BaseModel):
    item_id: str = Field(pattern=r"^item-\d{3}$")
    title: str = Field(min_length=1)
    content_type: Literal["news", "product", "model", "paper", "opinion", "tutorial", "interview"]
    event_cluster_id: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    essence: str = Field(min_length=1)
    spoken_segments: list[SpokenSegment] = Field(min_length=2, max_length=5)
    why_it_matters: str = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)
    suggested_action: Literal["open", "save", "skip", "deepen", "verify"]
    action_reason: str = Field(min_length=1)
    course_url: str | None = None

    _validate_course_url = field_validator("course_url")(lambda value: validate_link(value) if value else value)

    @model_validator(mode="after")
    def validate_segments(self):
        ids = [segment.segment_id for segment in self.spoken_segments]
        if len(ids) != len(set(ids)):
            raise ValueError(f"速听段 id 重复：{self.item_id}")
        for segment in self.spoken_segments:
            if segment.attribution == "AI" and segment.kind != "inference":
                raise ValueError(f"AI 归属只能用于 inference：{self.item_id}/{segment.segment_id}")
        return self


class BriefingBatch(BaseModel):
    version: int = 1
    batch_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    generated_at: str
    language: Literal["zh-CN"] = "zh-CN"
    sources: list[BriefingSource] = Field(min_length=1)
    items: list[BriefingItem] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_relations(self):
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id 必须唯一")
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("item_id 必须唯一")
        clusters = [item.event_cluster_id for item in self.items]
        if len(clusters) != len(set(clusters)):
            raise ValueError("同一 event_cluster 必须先聚合成一条速听卡")
        known = set(source_ids)
        for item in self.items:
            if not set(item.source_ids) <= known:
                raise ValueError(f"速听卡引用未知来源：{item.item_id}")
            for segment in item.spoken_segments:
                for ref in segment.source_refs:
                    if ref.source_id not in known or ref.source_id not in item.source_ids:
                        raise ValueError(f"证据引用不属于当前速听卡：{item.item_id}/{segment.segment_id}")
        return self


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audio_duration(path: Path) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return None
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


def safe_filename(item_id: str, segment_id: str) -> str:
    value = f"{item_id}-{segment_id}.mp3"
    if not re.fullmatch(r"[a-z0-9-]+\.mp3", value):
        raise ValueError("音频文件名包含不允许的字符")
    return value


async def synthesize_audio(
    batch: BriefingBatch,
    target: Path,
    voice: str,
    rate: str,
    pitch: str,
    force: bool = False,
) -> BriefingBatch:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("缺少 edge-tts，请运行：pip install -r requirements.txt") from exc

    audio_root = target / "audio"
    audio_root.mkdir(parents=True, exist_ok=True)
    for item in batch.items:
        for segment in item.spoken_segments:
            filename = safe_filename(item.item_id, segment.segment_id)
            destination = audio_root / filename
            if force or not destination.exists():
                temporary = destination.with_suffix(".tmp.mp3")
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        await edge_tts.Communicate(segment.text, voice, rate=rate, pitch=pitch).save(str(temporary))
                        os.replace(temporary, destination)
                        last_error = None
                        break
                    except Exception as exc:  # 网络/TTS 服务错误统一有限重试
                        last_error = exc
                        temporary.unlink(missing_ok=True)
                        if attempt < 2:
                            await asyncio.sleep(1.5 * (attempt + 1))
                if last_error is not None:
                    raise RuntimeError(f"语音生成失败：{item.item_id}/{segment.segment_id}: {last_error}")
            segment.audio_file = f"audio/{filename}"
            segment.audio_duration = audio_duration(destination)
    return BriefingBatch.model_validate(batch.model_dump())


def write_batch(batch: BriefingBatch, output_root: Path) -> Path:
    target = output_root / batch.batch_id
    write_json(target / "briefing.json", batch.model_dump())
    return target / "briefing.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True, help="已人工整理的速听内容 JSON")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--tts", action="store_true", help="使用 edge-tts 为每个 spoken segment 生成 MP3")
    parser.add_argument("--force-tts", action="store_true")
    parser.add_argument("--voice", default="zh-CN-YunxiNeural")
    parser.add_argument("--rate", default="+12%")
    parser.add_argument("--pitch", default="-4Hz")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8737)
    args = parser.parse_args()

    batch = BriefingBatch.model_validate_json(args.fixture.read_text(encoding="utf-8"))
    target = args.output_root / batch.batch_id
    if args.tts:
        batch = asyncio.run(synthesize_audio(batch, target, args.voice, args.rate, args.pitch, args.force_tts))
    path = write_batch(batch, args.output_root)
    print(f"资讯速听内容包已生成：{path}")
    if args.output_root.resolve() == DEFAULT_OUTPUT.resolve():
        url = f"http://localhost:{args.port}/prototype/briefing/?batch={batch.batch_id}"
        print(f"本地查看：{url}")
        if args.serve:
            try:
                from tools.serve_local import serve
            except ModuleNotFoundError:
                from serve_local import serve
            print(f"资讯速听页：{url}")
            serve(args.port)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
