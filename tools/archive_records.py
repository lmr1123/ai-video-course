#!/usr/bin/env python3
"""Archive generated courses and briefings for local web history."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HISTORY_ROOT = ROOT / "local-data" / "history"
DEFAULT_SITE_BASE_URL = "http://localhost:8737"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str, fallback: str = "record") -> str:
    text = re.sub(r"\s+", "-", value.strip().lower())
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:80] or fallback


def frontmatter(value: dict[str, Any]) -> str:
    lines = ["---"]
    for key, item in value.items():
        if isinstance(item, list):
            lines.append(f"{key}:")
            for entry in item:
                lines.append(f"  - {json.dumps(entry, ensure_ascii=False)}")
        elif item is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {json.dumps(item, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def absolute_web_url(path: str, site_base_url: str | None = None) -> str:
    if re.match(r"^https?://", path):
        return path
    base = (site_base_url or os.getenv("AI_VIDEO_COURSE_BASE_URL") or DEFAULT_SITE_BASE_URL).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def load_index(history_root: Path) -> dict[str, Any]:
    path = history_root / "index.json"
    if not path.exists():
        return {"version": 1, "updated_at": "", "records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_index(history_root: Path, record: dict[str, Any]) -> None:
    history_root.mkdir(parents=True, exist_ok=True)
    index = load_index(history_root)
    records = [item for item in index.get("records", []) if item.get("id") != record["id"]]
    records.append(record)
    records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    index = {"version": 1, "updated_at": now_iso(), "records": records}
    (history_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(
    history_root: Path,
    filename: str,
    markdown: str,
) -> Path:
    markdown_root = history_root / "notes"
    markdown_root.mkdir(parents=True, exist_ok=True)
    local_path = markdown_root / filename
    local_path.write_text(markdown, encoding="utf-8")
    return local_path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def quote_markdown(value: str) -> str:
    text = value.strip()
    if not text:
        return "> 当前内容包没有保存这段来源摘录。"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def briefing_source_ref_markdown(
    ref: dict[str, Any],
    source: dict[str, Any],
    full_email_available: bool = False,
) -> str:
    label = ref.get("label") or source.get("title") or ref.get("source_id") or "来源"
    published_at = source.get("published_at") or "日期未提供"
    account = source.get("account")
    source_meta = f"{label} · {published_at}"
    if account:
        source_meta += f" · {account}"
    lines = [f"- {source_meta}", f"  - 锚点：{ref.get('anchor_kind', 'unknown')} · {ref.get('locator', '')}"]
    excerpt_zh = ref.get("excerpt_zh", "").strip()
    excerpt = ref.get("excerpt", "").strip()
    readable = excerpt_zh or excerpt
    lines.append("")
    if readable:
        lines.append(quote_markdown(readable))
    elif full_email_available:
        lines.append("> 见下方完整邮件内容，可按段落编号核对。")
    else:
        lines.append(quote_markdown(readable))
    if excerpt_zh and excerpt:
        lines.append("")
        lines.append("  英文原文：")
        lines.append(quote_markdown(excerpt))
    if ref.get("url"):
        lines.append("")
        lines.append(f"  核对入口：{ref['url']}")
    return "\n".join(lines)


def collect_item_source_refs(item: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    seen = set()
    for segment in item["spoken_segments"]:
        for ref in segment["source_refs"]:
            key = (
                ref.get("source_id"),
                ref.get("anchor_kind"),
                ref.get("locator"),
                ref.get("excerpt_zh"),
                ref.get("excerpt"),
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def mail_drop_by_source(sources: dict[str, dict[str, Any]], mail_drop: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not mail_drop:
        return {}
    by_url = {message.get("gmail_url"): message for message in mail_drop.get("emails", []) if message.get("gmail_url")}
    by_title = {
        (message.get("subject", ""), message.get("from_name", "") or message.get("from_address", "")): message
        for message in mail_drop.get("emails", [])
    }
    matched: dict[str, dict[str, Any]] = {}
    for source_id, source in sources.items():
        message = by_url.get(source.get("url"))
        if message is None:
            message = by_title.get((source.get("title", ""), source.get("author", "")))
        if message is not None:
            matched[source_id] = message
    return matched


def paragraph_text(paragraph: dict[str, Any]) -> tuple[str, str]:
    if paragraph.get("text_zh"):
        return clean_email_paragraph(paragraph["text_zh"]), "中文"
    return clean_email_paragraph(paragraph.get("text", "")), "原文，尚未翻译"


def clean_email_paragraph(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    if is_mail_boilerplate(value):
        return ""
    value = re.sub(r"\s*\[\s*https?://[^\]]+\]", "", value)
    value = re.sub(r"https?://substack\.com/redirect/\S+", "", value)
    value = re.sub(r"https?://click\.[^\s\]]+", "", value)
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\s*\[\s*\]\s*", " ", value)
    return value.strip(" -–—\t")


def is_mail_boilerplate(text: str) -> bool:
    value = text.strip().lower()
    if not value:
        return True
    boilerplate_prefixes = (
        "view this post on the web",
        "unsubscribe",
        "manage your subscription",
        "you are receiving this email",
        "you received this email",
        "update your email preferences",
        "like",
        "comment",
        "share",
        "©",
        "-->",
    )
    if any(value.startswith(prefix) for prefix in boilerplate_prefixes):
        return True
    boilerplate_phrases = (
        "you can opt in/out",
        "email frequencies",
        "download the substack app",
        "thanks for reading",
    )
    return any(phrase in value for phrase in boilerplate_phrases)


def full_email_markdown(message: dict[str, Any] | None) -> str:
    if not message:
        return "> 当前归档没有找到这封邮件的完整正文。"
    paragraphs = []
    language = "中文"
    for paragraph in message.get("paragraphs", []):
        text, paragraph_language = paragraph_text(paragraph)
        if not text:
            continue
        if paragraph_language != "中文":
            language = paragraph_language
        paragraphs.append(f"> [{paragraph.get('index')}] {text}")
    if not paragraphs:
        return "> 当前归档找到邮件记录，但正文为空。"
    header = f"完整邮件内容（{language}）："
    return header + "\n" + "\n>\n".join(paragraphs)


def archive_course(
    course: Any,
    history_root: Path = DEFAULT_HISTORY_ROOT,
    site_base_url: str | None = None,
) -> dict[str, Any]:
    data = course.model_dump() if hasattr(course, "model_dump") else dict(course)
    created_at = now_iso()
    record_id = f"course-{data['video_id']}"
    filename = f"{record_id}-{slugify(data['course_title'])}.md"
    web_url = f"prototype/generated/viewer.html?id={data['video_id']}"
    markdown_web_url = absolute_web_url(web_url, site_base_url)
    tags = ["ai-video-course", "视频课堂", "本地历史"]

    claims = "\n".join(f"- {item['text']} ({format_seconds(item['start_sec'])})" for item in data["claims"])
    topics = "\n".join(
        f"- {format_seconds(item['start_sec'])}-{format_seconds(item['end_sec'])} · {item['action']} · {item['title']}"
        for item in data["topics"]
    )
    modules = "\n".join(
        f"- {item['title']}：{item['summary_30s']} ({format_seconds(item['start_sec'])}-{format_seconds(item['end_sec'])})"
        for item in data["deep_modules"]
    )
    markdown = frontmatter(
        {
            "type": "video_course",
            "source": "AI Video Course",
            "record_id": record_id,
            "created_at": created_at,
            "source_url": data["source_url"],
            "web_url": markdown_web_url,
            "tags": tags,
        }
    )
    markdown += f"# {data['course_title']}\n\n"
    markdown += f"> 本地历史记录：视频课堂生成记录。原视频：[{data['source_title']}]({data['source_url']})。\n\n"
    markdown += f"## 一句话\n\n{data['one_sentence']}\n\n"
    markdown += f"## 核心主张\n\n{claims}\n\n"
    markdown += f"## 知识地图\n\n{topics}\n\n"
    markdown += f"## 深讲模块\n\n{modules}\n\n"
    markdown += f"## 继续学习\n\n- [打开网页课程]({markdown_web_url})\n- [打开原视频]({data['source_url']})\n"

    local_path = write_markdown(history_root, filename, markdown)
    record = {
        "id": record_id,
        "type": "course",
        "title": data["course_title"],
        "created_at": created_at,
        "source_title": data["source_title"],
        "source_url": data["source_url"],
        "web_url": web_url,
        "summary": data["one_sentence"],
        "markdown_path": display_path(local_path),
        "tags": tags,
    }
    write_index(history_root, record)
    return record


def archive_briefing(
    batch: Any,
    history_root: Path = DEFAULT_HISTORY_ROOT,
    site_base_url: str | None = None,
    mail_drop: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)
    created_at = now_iso()
    record_id = f"briefing-{data['batch_id']}"
    filename = f"{record_id}-{slugify(data['title'])}.md"
    web_url = f"prototype/briefing/?batch={data['batch_id']}"
    markdown_web_url = absolute_web_url(web_url, site_base_url)
    tags = ["ai-video-course", "资讯速听", "本地历史"]
    sources = {source["source_id"]: source for source in data["sources"]}
    mail_messages = mail_drop_by_source(sources, mail_drop)

    items = []
    for item in data["items"]:
        first_source = sources.get(item["source_ids"][0], {})
        item_lines = [
            f"### {item['title']}",
            "",
            f"- 类型：{item['content_type']}",
            f"- 来源：[{first_source.get('title', '来源')}]({first_source.get('url', '')})",
            f"- 日期：{first_source.get('published_at') or '日期未提供'}",
            f"- 要点：{item['essence']}",
            f"- 判断：{item['why_it_matters']}",
            f"- 下一步：{item['action_reason']}",
            "",
            "短讲脚本：",
        ]
        for segment in item["spoken_segments"]:
            item_lines.append(f"- {segment['text']}")
        item_lines.append("")
        item_lines.append("来源内容：")
        for ref in collect_item_source_refs(item):
            item_lines.append(
                briefing_source_ref_markdown(
                    ref,
                    sources.get(ref["source_id"], {}),
                    full_email_available=ref["source_id"] in mail_messages,
                )
            )
        for source_id in item["source_ids"]:
            source = sources.get(source_id, {})
            item_lines.append("")
            item_lines.append(f"完整邮件：{source.get('title', source_id)}")
            item_lines.append(full_email_markdown(mail_messages.get(source_id)))
        items.append("\n".join(item_lines))

    markdown = frontmatter(
        {
            "type": "briefing_batch",
            "source": "AI Video Course",
            "record_id": record_id,
            "created_at": created_at,
            "web_url": markdown_web_url,
            "tags": tags,
        }
    )
    markdown += f"# {data['title']}\n\n"
    markdown += "> 本地历史记录：资讯速听生成记录。每条内容保留原文或邮件来源入口，方便回看和核对。\n\n"
    if data.get("intro_text"):
        markdown += f"## 批次导览\n\n{data['intro_text']}\n\n"
    markdown += f"## 本批内容\n\n{chr(10).join(items)}\n\n"
    markdown += f"## 继续查看\n\n- [打开网页速听]({markdown_web_url})\n"

    local_path = write_markdown(history_root, filename, markdown)
    record = {
        "id": record_id,
        "type": "briefing",
        "title": data["title"],
        "created_at": created_at,
        "source_title": f"{len(data['items'])} 条资讯",
        "source_url": web_url,
        "web_url": web_url,
        "summary": data.get("intro_text") or "；".join(item["essence"] for item in data["items"][:3]),
        "markdown_path": display_path(local_path),
        "tags": tags,
    }
    write_index(history_root, record)
    return record


def format_seconds(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
