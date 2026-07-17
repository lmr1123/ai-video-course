#!/usr/bin/env python3
"""定时执行 Gmail → DeepSeek → 资讯速听 → TTS → 私有服务器同步。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.briefing_pipeline import (
    BriefingBatch,
    BriefingItem,
    BriefingSource,
    SourceRef,
    SpokenSegment,
    synthesize_audio,
    write_batch,
)
from tools.gmail_ingest import fetch_mail_drop
from tools.model_runtime import (
    add_attempt,
    estimate_tokens_from_chars,
    finish_ledger,
    new_ledger,
    stable_hash,
    write_json_atomic,
)


DEFAULT_CONFIG = ROOT / "local-data" / "briefing" / "config.json"
DEFAULT_ENV = ROOT / ".env"
DEFAULT_OUTPUT = ROOT / "local-data" / "briefing"
ALLOWED_ACTIONS = Literal["open", "save", "skip", "deepen", "verify"]
ALLOWED_TYPES = Literal["news", "product", "model", "paper", "opinion", "tutorial", "interview"]
MAIL_PROMPT_VERSION = "mail-briefing-v1"
MODEL_OUTPUT_TOKEN_LIMIT = 5000
SKIP_SUBJECT_PATTERNS = ("verification code", "验证码", "unsubscribe", "退订")


class ScheduleConfig(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    timezone: str = "Asia/Shanghai"


class GmailConfig(BaseModel):
    senders: list[str] = Field(min_length=1)
    lookback_hours: int = Field(default=24, ge=1, le=168)
    max_per_sender: int = Field(default=20, ge=1, le=100)


class ModelConfig(BaseModel):
    provider: Literal["deepseek"] = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    max_items: int = Field(default=10, ge=1, le=20)
    max_items_per_email: int = Field(default=2, ge=1, le=5)
    max_chars_per_email: int = Field(default=50000, ge=2000, le=200000)
    max_candidate_emails: int = Field(default=10, ge=1, le=50)
    max_model_calls_per_run: int = Field(default=10, ge=1, le=50)
    max_total_input_chars: int = Field(default=200000, ge=2000, le=2000000)
    max_estimated_input_tokens: int = Field(default=100000, ge=1000, le=1000000)
    max_estimated_output_tokens: int = Field(default=50000, ge=1000, le=500000)


class TTSConfig(BaseModel):
    enabled: bool = True
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+4%"
    pitch: str = "-2Hz"


class DeployConfig(BaseModel):
    enabled: bool = True
    ssh_host: str = "ker-cloud"
    remote_root: str = "/home/ubuntu/ai-briefing-site"
    public_url: str

    @field_validator("ssh_host")
    @classmethod
    def safe_host(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("ssh_host 包含不允许的字符")
        return value

    @field_validator("remote_root")
    @classmethod
    def safe_root(cls, value: str) -> str:
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value:
            raise ValueError("remote_root 必须是安全的绝对路径")
        return value.rstrip("/")


class JobConfig(BaseModel):
    schedule: ScheduleConfig
    gmail: GmailConfig
    model: ModelConfig = Field(default_factory=ModelConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    deploy: DeployConfig


class GeneratedRef(BaseModel):
    paragraph_index: int = Field(ge=1)
    label: str = Field(min_length=1)
    excerpt_zh: str = ""


class GeneratedSegment(BaseModel):
    text: str = Field(min_length=1)
    kind: Literal["fact", "opinion", "inference"]
    attribution: Literal["source", "AI"]
    refs: list[GeneratedRef] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_attribution(self):
        if self.attribution == "AI" and self.kind != "inference":
            raise ValueError("AI 内容必须标记为 inference")
        return self


class GeneratedItem(BaseModel):
    title: str = Field(min_length=1)
    content_type: ALLOWED_TYPES
    event_cluster_id: str = Field(min_length=1)
    essence: str = Field(min_length=1)
    priority: int = Field(ge=1, le=5)
    spoken_segments: list[GeneratedSegment] = Field(min_length=4, max_length=4)
    why_it_matters: str = Field(min_length=1)
    caveats: list[str] = Field(default_factory=list)
    suggested_action: ALLOWED_ACTIONS
    action_reason: str = Field(min_length=1)


class GeneratedEmail(BaseModel):
    items: list[GeneratedItem]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def load_config(path: Path) -> JobConfig:
    return JobConfig.model_validate_json(path.read_text(encoding="utf-8"))


def source_id_for(message: dict) -> str:
    stable = message.get("message_id") or "|".join(
        [message.get("from_address", ""), message.get("subject", ""), message.get("published_at", "")]
    )
    return f"source-{hashlib.sha256(stable.encode()).hexdigest()[:12]}"


def compact_paragraphs(message: dict, max_chars: int) -> tuple[list[dict], bool]:
    selected: list[dict] = []
    used = 0
    for paragraph in message.get("paragraphs", []):
        text = paragraph.get("text", "").strip()
        if not text:
            continue
        cost = len(text) + 24
        if selected and used + cost > max_chars:
            return selected, True
        selected.append({"index": paragraph["index"], "text": text})
        used += cost
    return selected, False


def generation_prompt(message: dict, source_id: str, config: ModelConfig) -> str:
    paragraphs, truncated = compact_paragraphs(message, config.max_chars_per_email)
    payload = {
        "source_id": source_id,
        "sender": message.get("from_name") or message.get("from_address"),
        "subject": message.get("subject"),
        "published_at": message.get("published_at"),
        "truncated": truncated,
        "paragraphs": paragraphs,
    }
    return f"""请从下面一封邮件中提炼最多 {config.max_items_per_email} 条值得收听的 AI 资讯，并只输出 JSON。

目标听众第一次接触这件事。每条必须恰好四段：
1. 主体与背景：谁在讲，这是什么内容；
2. 内容与主张：发生了什么或作者表达了什么；
3. 原理或趋势：用通俗语言解释机制、观点或趋势；
4. 判断与限定：它传递了什么，以及证据边界。

规则：
- 事实和作者观点 attribution=source；你的解释和判断 attribution=AI 且 kind=inference。
- 每段引用一个或多个真实 paragraph_index，不得引用不存在的段落。
- 不把广告、验证码、活动提醒和纯促销单独生成资讯。
- event_cluster_id 使用简短英文小写连字符 slug，让不同邮件的同一事件尽量一致。
- priority 1 到 5，5 表示最值得保留。
- refs 中每个引用必须包含 excerpt_zh：把对应英文或中文邮件段落翻译/整理为简洁中文，保留关键数字、模型名、人名和限定，不要加入段落没有的信息。
- JSON 格式示例：{{"items":[{{"title":"...","content_type":"news","event_cluster_id":"event-slug","essence":"...","priority":4,"spoken_segments":[{{"text":"...","kind":"fact","attribution":"source","refs":[{{"paragraph_index":3,"label":"发布背景","excerpt_zh":"该段说明了发布背景。"}}]}}],"why_it_matters":"...","caveats":["..."],"suggested_action":"open","action_reason":"..."}}]}}

邮件 JSON：
{json.dumps(payload, ensure_ascii=False)}"""


def prepare_messages(messages: list[dict], config: ModelConfig) -> tuple[list[dict], list[dict], dict[str, int]]:
    selected: list[dict] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for message in messages:
        subject = str(message.get("subject") or "")
        if any(pattern in subject.lower() for pattern in SKIP_SUBJECT_PATTERNS):
            skipped.append({"subject": subject, "reason": "deterministic_filter"})
            continue
        paragraphs, _ = compact_paragraphs(message, config.max_chars_per_email)
        fingerprint = stable_hash({"subject": subject, "paragraphs": paragraphs})
        if fingerprint in seen:
            skipped.append({"subject": subject, "reason": "duplicate_content"})
            continue
        seen.add(fingerprint)
        if len(selected) >= config.max_candidate_emails:
            skipped.append({"subject": subject, "reason": "candidate_limit"})
            continue
        selected.append(message)

    input_chars = sum(len(generation_prompt(message, source_id_for(message), config)) for message in selected)
    estimated = {
        "model_calls": len(selected),
        "input_chars": input_chars,
        "input_tokens": estimate_tokens_from_chars(input_chars),
        "output_tokens": len(selected) * MODEL_OUTPUT_TOKEN_LIMIT,
    }
    limits = {
        "model_calls": config.max_model_calls_per_run,
        "input_chars": config.max_total_input_chars,
        "input_tokens": config.max_estimated_input_tokens,
        "output_tokens": config.max_estimated_output_tokens,
    }
    exceeded = [key for key, value in estimated.items() if value > limits[key]]
    if exceeded:
        detail = ", ".join(f"{key}={estimated[key]}>{limits[key]}" for key in exceeded)
        raise RuntimeError(f"模型调用前预算检查失败：{detail}；未调用模型")
    return selected, skipped, estimated


def deepseek_client(api_key: str, base_url: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai SDK，请运行 pip install -r requirements.txt") from exc
    return OpenAI(api_key=api_key, base_url=base_url)


def generate_email_items(
    client,
    message: dict,
    source_id: str,
    config: ModelConfig,
    *,
    cache_root: Path,
    ledger: dict,
) -> list[GeneratedItem]:
    prompt = generation_prompt(message, source_id, config)
    cache_key = stable_hash({
        "prompt_version": MAIL_PROMPT_VERSION,
        "model": config.model,
        "prompt": prompt,
    })
    cache_path = cache_root / f"{cache_key}.json"
    if cache_path.exists():
        cached = GeneratedEmail.model_validate_json(cache_path.read_text(encoding="utf-8"))
        ledger["cache_hits"] += 1
        return cached.items
    ledger["cache_misses"] += 1
    last_error: Exception | None = None
    for attempt in range(2):
        response = None
        try:
            response = client.chat.completions.create(
                model=config.model,
                messages=[
                    {"role": "system", "content": "你是可追溯资讯编辑。必须输出合法 JSON，不要输出 Markdown。"},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=MODEL_OUTPUT_TOKEN_LIMIT,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = response.choices[0].message.content or ""
            generated = GeneratedEmail.model_validate_json(content)
            known = {paragraph["index"] for paragraph in message.get("paragraphs", [])}
            for item in generated.items:
                for segment in item.spoken_segments:
                    for ref in segment.refs:
                        if ref.paragraph_index not in known:
                            raise ValueError(f"模型引用不存在的邮件段落：{ref.paragraph_index}")
            add_attempt(
                ledger,
                unit=source_id,
                attempt=attempt + 1,
                status="success",
                usage=response.usage,
            )
            write_json_atomic(cache_path, generated.model_dump())
            return generated.items
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            add_attempt(
                ledger,
                unit=source_id,
                attempt=attempt + 1,
                status="validation_error",
                usage=getattr(response, "usage", None),
                error=str(exc),
            )
            prompt += f"\n上次 JSON 校验失败：{exc}。请修正后重新输出完整 JSON。"
        except Exception as exc:
            add_attempt(
                ledger,
                unit=source_id,
                attempt=attempt + 1,
                status="request_error",
                usage=getattr(response, "usage", None),
                error=str(exc),
            )
            raise
    raise RuntimeError(f"DeepSeek 输出连续两次未通过校验：{last_error}")


def build_batch(drop: dict, generated: list[tuple[dict, list[GeneratedItem]]], config: ModelConfig) -> BriefingBatch:
    candidates: list[tuple[datetime, dict, GeneratedItem]] = []
    sources: dict[str, BriefingSource] = {}
    for message, items in generated:
        source_id = source_id_for(message)
        _, truncated = compact_paragraphs(message, config.max_chars_per_email)
        source = BriefingSource(
            source_id=source_id,
            kind="email",
            title=message.get("subject") or "无主题邮件",
            url=message.get("gmail_url") or "https://mail.google.com/mail/u/0/#inbox",
            author=message.get("from_name") or message.get("from_address", ""),
            published_at=(message.get("published_at") or "")[:10],
            extraction_status="partial" if truncated else message.get("extraction_status", "partial"),
        )
        sources[source_id] = source
        try:
            published = datetime.fromisoformat(message.get("published_at", ""))
        except ValueError:
            published = datetime.min.replace(tzinfo=timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        for item in items:
            candidates.append((published, message, item))

    candidates.sort(key=lambda value: (value[2].priority, value[0]), reverse=True)
    deduped: list[tuple[dict, GeneratedItem]] = []
    seen: set[str] = set()
    for _, message, item in candidates:
        cluster = re.sub(r"[^a-z0-9-]+", "-", item.event_cluster_id.lower()).strip("-") or "uncategorized"
        if cluster in seen:
            continue
        seen.add(cluster)
        item.event_cluster_id = cluster
        deduped.append((message, item))
        if len(deduped) >= config.max_items:
            break
    if not deduped:
        raise RuntimeError("邮件中没有生成可播报资讯")

    items: list[BriefingItem] = []
    used_sources: set[str] = set()
    for index, (message, generated_item) in enumerate(deduped, 1):
        source_id = source_id_for(message)
        used_sources.add(source_id)
        paragraph_by_index = {
            paragraph["index"]: paragraph.get("text", "").strip()
            for paragraph in message.get("paragraphs", [])
        }
        segments: list[SpokenSegment] = []
        for segment_index, segment in enumerate(generated_item.spoken_segments, 1):
            refs = [
                SourceRef(
                    source_id=source_id,
                    label=ref.label,
                    anchor_kind="email_paragraph",
                    locator=f"paragraph-{ref.paragraph_index}",
                    url=sources[source_id].url,
                    excerpt=paragraph_by_index.get(ref.paragraph_index, ""),
                    excerpt_zh=ref.excerpt_zh,
                )
                for ref in segment.refs
            ]
            segments.append(
                SpokenSegment(
                    segment_id=f"segment-{segment_index:02d}",
                    text=segment.text,
                    kind=segment.kind,
                    attribution=segment.attribution,
                    source_refs=refs,
                )
            )
        items.append(
            BriefingItem(
                item_id=f"item-{index:03d}",
                title=generated_item.title,
                content_type=generated_item.content_type,
                event_cluster_id=generated_item.event_cluster_id,
                source_ids=[source_id],
                essence=generated_item.essence,
                spoken_segments=segments,
                why_it_matters=generated_item.why_it_matters,
                caveats=generated_item.caveats,
                suggested_action=generated_item.suggested_action,
                action_reason=generated_item.action_reason,
                transition_text="" if index == 1 else f"接下来第 {index} 条，{generated_item.title}。",
            )
        )

    local_now = datetime.now().astimezone()
    themes = "、".join(item.title for item in items[:4])
    return BriefingBatch(
        batch_id=f"mail-auto-{local_now.strftime('%Y%m%d-%H%M%S')}",
        title=f"AI 邮件速听 · {local_now.strftime('%m月%d日')}",
        generated_at=local_now.isoformat(),
        intro_text=f"今天带来 {len(items)} 条内容，主要包括 {themes}。下面开始。",
        sources=[sources[source_id] for source_id in sources if source_id in used_sources],
        items=items,
    )


def deploy_batch(batch_dir: Path, config: DeployConfig) -> str:
    root = config.remote_root
    host = config.ssh_host
    subprocess.run(
        [
            "ssh",
            host,
            "mkdir",
            "-p",
            f"{root}/prototype/briefing",
            f"{root}/prototype/history",
            f"{root}/prototype/relecture",
            f"{root}/prototype/generated",
            f"{root}/local-data/briefing",
            f"{root}/local-data/history",
        ],
        check=True,
    )
    page_assets = (
        (ROOT / "prototype" / "index.html", f"{root}/prototype/index.html"),
        (ROOT / "prototype" / "briefing" / "index.html", f"{root}/prototype/briefing/index.html"),
        (ROOT / "prototype" / "history" / "index.html", f"{root}/prototype/history/index.html"),
        (ROOT / "prototype" / "theme.css", f"{root}/prototype/theme.css"),
    )
    for local_path, remote_path in page_assets:
        subprocess.run(["rsync", "-az", str(local_path), f"{host}:{remote_path}"], check=True)
    for directory in ("relecture", "generated"):
        subprocess.run(
            ["rsync", "-az", "--delete", f"{ROOT}/prototype/{directory}/", f"{host}:{root}/prototype/{directory}/"],
            check=True,
        )
    history_dir = ROOT / "local-data" / "history"
    if history_dir.is_dir():
        subprocess.run(
            ["rsync", "-az", "--delete", f"{history_dir}/", f"{host}:{root}/local-data/history/"],
            check=True,
        )
    subprocess.run(["rsync", "-az", "--delete", f"{batch_dir}/", f"{host}:{root}/local-data/briefing/{batch_dir.name}/"], check=True)
    subprocess.run(
        ["rsync", "-az", str(batch_dir / "briefing.json"), f"{host}:{root}/prototype/briefing/briefing.json"],
        check=True,
    )
    audio_dir = batch_dir / "audio"
    if audio_dir.is_dir():
        subprocess.run(
            ["rsync", "-az", "--delete", f"{audio_dir}/", f"{host}:{root}/prototype/briefing/audio/"],
            check=True,
        )
    subprocess.run(
        ["ssh", host, "ln", "-sfn", batch_dir.name, f"{root}/local-data/briefing/latest"],
        check=True,
    )
    return f"{config.public_url.rstrip('/')}/prototype/briefing/index.html"


def run_job(config_path: Path, env_path: Path, skip_tts: bool = False) -> Path | None:
    load_env(env_path)
    config = load_config(config_path)
    run_id = f"mail-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f')}"
    usage_path = DEFAULT_OUTPUT / "usage" / f"{run_id}.json"
    ledger = new_ledger("mail_briefing", config.model.model, MAIL_PROMPT_VERSION)
    try:
        required = ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "DEEPSEEK_API_KEY"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"缺少环境变量：{', '.join(missing)}")
        since = datetime.now(timezone.utc) - timedelta(hours=config.gmail.lookback_hours)
        drop = fetch_mail_drop(
            os.environ["GMAIL_ADDRESS"],
            os.environ["GMAIL_APP_PASSWORD"],
            sorted(set(config.gmail.senders)),
            since,
            config.gmail.max_per_sender,
        )
        if not drop["emails"]:
            print("时间窗口内没有新邮件，不生成空批次")
            finish_ledger(ledger, "success")
            write_json_atomic(usage_path, ledger)
            return None

        selected, skipped, estimated = prepare_messages(drop["emails"], config.model)
        ledger["estimated"] = estimated
        ledger["skipped"] = skipped
        if not selected:
            print("确定性筛选后没有需要生成的邮件，不调用模型")
            finish_ledger(ledger, "success")
            write_json_atomic(usage_path, ledger)
            return None
        client = deepseek_client(os.environ["DEEPSEEK_API_KEY"], config.model.base_url)
        generated: list[tuple[dict, list[GeneratedItem]]] = []
        cache_root = DEFAULT_OUTPUT / "model-cache" / "mail"
        for message in selected:
            source_id = source_id_for(message)
            items = generate_email_items(
                client,
                message,
                source_id,
                config.model,
                cache_root=cache_root,
                ledger=ledger,
            )
            generated.append((message, items))
            print(f"DeepSeek：{message.get('subject', '无主题')} → {len(items)} 条")

        batch = build_batch(drop, generated, config.model)
        target = DEFAULT_OUTPUT / batch.batch_id
        if config.tts.enabled and not skip_tts:
            batch = asyncio.run(
                synthesize_audio(batch, target, config.tts.voice, config.tts.rate, config.tts.pitch)
            )
        output = write_batch(batch, DEFAULT_OUTPUT)
        finish_ledger(ledger, "success")
        write_json_atomic(usage_path, ledger)
        write_json_atomic(target / "usage.json", ledger)
        print(f"内容包：{output}")
        print(f"Token：{ledger['totals']}")
        if config.deploy.enabled:
            print(f"手机地址：{deploy_batch(target, config.deploy)}")
        return output
    except Exception as exc:
        finish_ledger(ledger, "failed", str(exc))
        write_json_atomic(usage_path, ledger)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail 定时资讯速听任务")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--skip-tts", action="store_true")
    parser.add_argument("--deploy-existing", type=Path, help="只同步已有批次目录，不读取 Gmail 或调用模型")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.deploy_existing:
        print(f"手机地址：{deploy_batch(args.deploy_existing.resolve(), config.deploy)}")
        return
    run_job(args.config, args.env, args.skip_tts)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, ValidationError, subprocess.CalledProcessError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
