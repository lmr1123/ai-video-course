#!/usr/bin/env python3
"""从 YouTube 链接生成可部署的静态课程数据。

真实生成：
  ZHIPU_API_KEY=... python3 tools/course_pipeline.py https://youtu.be/VIDEO_ID

离线验证：
  python3 tools/course_pipeline.py --fixture tests/fixtures/course.json --output-root /tmp/course-output
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.model_runtime import (
    add_attempt,
    estimate_tokens_from_chars,
    finish_ledger,
    new_ledger,
    stable_hash,
    write_json_atomic,
)

DEFAULT_OUTPUT = ROOT / "prototype" / "generated"
DEFAULT_CACHE = ROOT / "local-data" / "model-cache" / "course"
DEFAULT_USAGE = ROOT / "local-data" / "usage" / "course"
DEFAULT_ENV = ROOT / ".env"
DEFAULT_MODEL = "glm-5.2"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
MAX_OUTPUT_TOKENS = 16000
COURSE_PROMPT_VERSION = "course-v2-glm-5.2"


class Claim(BaseModel):
    text: str
    start_sec: int = Field(ge=0)


class Topic(BaseModel):
    title: str
    start_sec: int = Field(ge=0)
    end_sec: int = Field(gt=0)
    action: Literal["深讲", "快讲", "原片", "摘要", "跳过"]
    importance: Literal["高", "中", "低"]
    difficulty: Literal["高", "中", "低"]
    core: str
    why: str


class Evidence(BaseModel):
    attribution: Literal["原话", "归纳", "解释", "外部"]
    text: str
    start_sec: int | None = Field(default=None, ge=0)


class DeepModule(BaseModel):
    title: str
    start_sec: int = Field(ge=0)
    end_sec: int = Field(gt=0)
    content_type: str
    learning_goal: str
    summary_30s: str
    problem: str
    author_judgment: list[Evidence]
    plain_explanation: str
    mechanism: str
    cases: list[Evidence]
    value: str
    practice: str
    technical_deep_dive: str


class Course(BaseModel):
    video_id: str = Field(pattern=r"^[\w-]{11}$")
    source_url: str
    source_title: str
    channel: str
    duration_sec: int = Field(gt=0)
    course_title: str
    one_sentence: str
    audience: str
    prerequisites: list[str]
    expected_gain: list[str]
    claims: list[Claim] = Field(min_length=3, max_length=5)
    topics: list[Topic] = Field(min_length=5, max_length=12)
    deep_modules: list[DeepModule] = Field(min_length=2, max_length=3)
    recall_prompt: str
    recall_points: list[str] = Field(min_length=3, max_length=5)

    @model_validator(mode="after")
    def validate_timeline(self):
        starts = [topic.start_sec for topic in self.topics]
        if starts != sorted(starts):
            raise ValueError("topics 必须按 start_sec 排序")
        for topic in self.topics:
            if topic.end_sec <= topic.start_sec or topic.end_sec > self.duration_sec + 30:
                raise ValueError(f"主题时间范围无效：{topic.title}")
        deep_starts = {topic.start_sec for topic in self.topics if topic.action == "深讲"}
        for module in self.deep_modules:
            if module.end_sec <= module.start_sec or module.end_sec > self.duration_sec + 30:
                raise ValueError(f"深讲时间范围无效：{module.title}")
            if module.start_sec not in deep_starts:
                raise ValueError(f"深讲模块未对应知识地图：{module.title}")
            for evidence in module.author_judgment + module.cases:
                if evidence.attribution != "外部" and evidence.start_sec is None:
                    raise ValueError(f"视频内证据缺少时间戳：{module.title}")
        return self


def load_env(path: Path = DEFAULT_ENV) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def model_client(api_key: str, base_url: str = DEFAULT_BASE_URL):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai SDK，请运行：pip install -r requirements.txt") from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "命令执行失败")
    return result.stdout


def extract_video_id(url: str) -> str:
    match = re.search(r"(?:youtu\.be/|[?&]v=|embed/|shorts/)([\w-]{11})", url)
    if not match:
        raise ValueError("无法从 URL 识别 YouTube video id")
    return match.group(1)


def fetch_source(url: str) -> tuple[dict, list[dict]]:
    """获取元数据与原始英文字幕；不下载视频。"""
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
        caption_json = json.loads(files[0].read_text(encoding="utf-8"))
    return metadata, normalize_captions(caption_json)


def normalize_captions(data: dict) -> list[dict]:
    """过滤 json3 的换行追加事件，并合并成适合长上下文分析的时间段。"""
    raw = []
    for event in data.get("events", []):
        if event.get("aAppend") or not event.get("segs"):
            continue
        text = "".join(seg.get("utf8", "") for seg in event["segs"]).strip()
        if not text:
            continue
        start = int(event.get("tStartMs", 0) / 1000)
        end = int((event.get("tStartMs", 0) + event.get("dDurationMs", 0)) / 1000)
        raw.append({"start": start, "end": max(start + 1, end), "text": text})
    groups: list[dict] = []
    for cue in raw:
        if not groups or cue["start"] - groups[-1]["start"] >= 30 or len(groups[-1]["text"]) > 900:
            groups.append(cue.copy())
        else:
            groups[-1]["end"] = cue["end"]
            groups[-1]["text"] += " " + cue["text"]
    return groups


def timestamp(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def transcript_for_prompt(cues: list[dict]) -> str:
    return "\n".join(
        f"[{timestamp(cue['start'])}–{timestamp(cue['end'])}] {cue['text']}" for cue in cues
    )


def generate_course_from_source(
    url: str,
    model: str,
    metadata: dict,
    cues: list[dict],
    client,
    *,
    cache_root: Path,
    ledger: dict,
) -> Course:
    video_id = extract_video_id(url)
    duration = int(metadata.get("duration") or (cues[-1]["end"] if cues else 0))
    context = {
        "video_id": video_id,
        "source_url": url,
        "source_title": metadata.get("title", ""),
        "channel": metadata.get("channel") or metadata.get("uploader") or "",
        "duration_sec": duration,
    }
    instructions = """你是 AI Engineering 课程编排师，面向有基础项目经验的 vibe coding 开发者。
把技术长视频重构为中文课程，不做逐段摘要。必须：
1. 按语义切成 5–12 个连续主题，时间戳只能来自输入字幕；
2. 选择 2–3 个最重要、可迁移、证据充分的主题深讲；
3. 区分原话、归纳、解释、外部补充；视频内内容必须带 start_sec；
4. 深讲回答问题、作者判断、人话解释、机制、案例、价值、最小实践和技术深入；
5. 输出可验证的闭卷复述问题与 3–5 个核心检查点；
6. 不虚构作者、产品、数字或时间戳；
7. 只输出符合给定 JSON Schema 的合法 JSON，不要 Markdown 代码块或额外说明。"""
    schema = json.dumps(Course.model_json_schema(), ensure_ascii=False)
    payload = (
        json.dumps(context, ensure_ascii=False)
        + "\n\n带时间戳英文字幕：\n"
        + transcript_for_prompt(cues)
        + "\n\n必须遵守的 JSON Schema：\n"
        + schema
    )
    cache_key = stable_hash({
        "prompt_version": COURSE_PROMPT_VERSION,
        "model": model,
        "instructions": instructions,
        "payload": payload,
    })
    cache_path = cache_root / f"{cache_key}.json"
    ledger["estimated"] = {
        "input_chars": len(instructions) + len(payload),
        "input_tokens": estimate_tokens_from_chars(len(instructions) + len(payload)),
        "output_tokens": MAX_OUTPUT_TOKENS,
    }
    if cache_path.exists():
        ledger["cache_hits"] += 1
        return Course.model_validate_json(cache_path.read_text(encoding="utf-8"))
    ledger["cache_misses"] += 1
    prompt = payload
    last_error: Exception | None = None
    for attempt in range(2):
        response = None
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=MAX_OUTPUT_TOKENS,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
            course = Course.model_validate_json(response.choices[0].message.content or "")
            # 来源字段由程序写入，避免模型改写视频身份。
            course.video_id = context["video_id"]
            course.source_url = context["source_url"]
            course.source_title = context["source_title"]
            course.channel = context["channel"]
            course.duration_sec = context["duration_sec"]
            course = Course.model_validate(course.model_dump())
            add_attempt(
                ledger,
                unit=video_id,
                attempt=attempt + 1,
                status="success",
                usage=response.usage,
            )
            write_json_atomic(cache_path, course.model_dump())
            return course
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            add_attempt(
                ledger,
                unit=video_id,
                attempt=attempt + 1,
                status="validation_error",
                usage=getattr(response, "usage", None),
                error=str(exc),
            )
            prompt = payload + f"\n\n上次输出没有通过校验：{exc}。请修正后重新输出完整 JSON。"
        except Exception as exc:
            add_attempt(
                ledger,
                unit=video_id,
                attempt=attempt + 1,
                status="request_error",
                usage=getattr(response, "usage", None),
                error=str(exc),
            )
            raise
    raise RuntimeError(f"模型输出连续两次未通过课程校验：{last_error}")


def generate_course(
    url: str,
    model: str,
    *,
    cache_root: Path = DEFAULT_CACHE,
    ledger: dict | None = None,
    client=None,
) -> Course:
    if client is None:
        if not os.getenv("ZHIPU_API_KEY"):
            raise RuntimeError("缺少 ZHIPU_API_KEY；请只在本机 .env 中配置")
        client = model_client(
            os.environ["ZHIPU_API_KEY"],
            os.getenv("ZHIPU_BASE_URL", DEFAULT_BASE_URL),
        )
    metadata, cues = fetch_source(url)
    active_ledger = ledger if ledger is not None else new_ledger("course", model, COURSE_PROMPT_VERSION)
    return generate_course_from_source(
        url,
        model,
        metadata,
        cues,
        client,
        cache_root=cache_root,
        ledger=active_ledger,
    )


def write_course(course: Course, output_root: Path) -> Path:
    target = output_root / course.video_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "course.json").write_text(
        json.dumps(course.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path = output_root / "manifest.json"
    manifest = {"courses": []}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    item = {"video_id": course.video_id, "title": course.course_title, "path": f"{course.video_id}/course.json"}
    manifest["courses"] = [c for c in manifest.get("courses", []) if c.get("video_id") != course.video_id]
    manifest["courses"].append(item)
    manifest["courses"].sort(key=lambda c: c["video_id"])
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target / "course.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?")
    parser.add_argument("--model")
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--fixture", type=Path, help="跳过下载和 API，验证已有课程 JSON")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=ROOT / "local-data" / "history")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--usage-root", type=Path, default=DEFAULT_USAGE)
    parser.add_argument("--site-base-url", default=os.getenv("AI_VIDEO_COURSE_BASE_URL"), help="写入历史 Markdown 的网页基准地址")
    parser.add_argument("--no-archive", action="store_true", help="不写入本地历史记录")
    args = parser.parse_args()
    if args.fixture:
        course = Course.model_validate_json(args.fixture.read_text(encoding="utf-8"))
    elif args.url:
        load_env(args.env_path)
        model = args.model or os.getenv("COURSE_MODEL", DEFAULT_MODEL)
        ledger = new_ledger("course", model, COURSE_PROMPT_VERSION)
        usage_path = args.usage_root / f"{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}.json"
        try:
            course = generate_course(args.url, model, cache_root=args.cache_root, ledger=ledger)
            finish_ledger(ledger, "success")
            write_json_atomic(usage_path, ledger)
        except Exception as exc:
            finish_ledger(ledger, "failed", str(exc))
            write_json_atomic(usage_path, ledger)
            raise
    else:
        parser.error("请提供 YouTube URL 或 --fixture")
    path = write_course(course, args.output_root)
    print(f"课程已生成：{path}")
    if not args.no_archive:
        try:
            from tools.archive_records import archive_course
        except ModuleNotFoundError:
            from archive_records import archive_course
        record = archive_course(
            course,
            history_root=args.history_root,
            site_base_url=args.site_base_url,
        )
        print(f"历史记录已写入：{record['markdown_path']}")
    if args.output_root.resolve() == DEFAULT_OUTPUT.resolve():
        print(f"本地查看：http://localhost:8737/generated/viewer.html?id={course.video_id}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
