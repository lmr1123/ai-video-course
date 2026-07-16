#!/usr/bin/env python3
"""按发件人白名单从 Gmail 拉取邮件，输出资讯速听可用的标准化邮件包。

凭据只从环境变量读取，绝不写入仓库：
  export GMAIL_ADDRESS="you@gmail.com"
  export GMAIL_APP_PASSWORD="应用专用密码"

用法：
  python3 tools/gmail_ingest.py --senders news@example.com,ai@example.org --days 7
  python3 tools/gmail_ingest.py --senders-file local-data/briefing/senders.txt

输出 local-data/briefing/mail-inbox/<日期>-mail-drop.json：每封邮件带
message_id、发件人、主题、时间、按段落编号的正文和 Gmail 原文链接，
供后续提炼成 briefing.json（段落编号即 email_paragraph 锚点的 locator）。
"""

from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "local-data" / "briefing" / "mail-inbox"
IMAP_HOST = "imap.gmail.com"

BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "table"}
SKIP_TAGS = {"script", "style", "head", "title"}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.chunks.append(data)

    def text(self) -> str:
        return "".join(self.chunks)


def html_to_text(html: str) -> str:
    extractor = TextExtractor()
    extractor.feed(html)
    return extractor.text()


def split_paragraphs(text: str) -> list[str]:
    normalized = re.sub(r"[ \t​ ]+", " ", text)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", normalized)]
    return [part for part in paragraphs if len(part) > 1]


def message_body(message: email.message.EmailMessage) -> tuple[str, str]:
    """返回 (正文文本, 提取状态 complete|partial)。优先纯文本，退回 HTML。"""
    plain = message.get_body(preferencelist=("plain",))
    if plain is not None:
        return plain.get_content(), "complete"
    html = message.get_body(preferencelist=("html",))
    if html is not None:
        return html_to_text(html.get_content()), "complete"
    return "", "partial"


def gmail_permalink(message_id: str) -> str:
    query = urllib.parse.quote(f"rfc822msgid:{message_id.strip('<>')}")
    return f"https://mail.google.com/mail/u/0/#search/{query}"


def normalize(message: email.message.EmailMessage) -> dict:
    body, status = message_body(message)
    paragraphs = split_paragraphs(body)
    if not paragraphs:
        status = "partial"
    message_id = message.get("Message-ID", "").strip()
    date_header = message.get("Date", "")
    try:
        published = email.utils.parsedate_to_datetime(date_header).isoformat()
    except (TypeError, ValueError):
        published = date_header
    sender_name, sender_addr = email.utils.parseaddr(message.get("From", ""))
    return {
        "message_id": message_id,
        "from_name": sender_name,
        "from_address": sender_addr.lower(),
        "subject": message.get("Subject", "(无主题)"),
        "published_at": published,
        "extraction_status": status,
        "gmail_url": gmail_permalink(message_id) if message_id else "https://mail.google.com/mail/u/0/#inbox",
        "paragraphs": [
            {"index": index + 1, "text": text}
            for index, text in enumerate(paragraphs)
        ],
    }


def fetch_from_sender(client: imaplib.IMAP4_SSL, sender: str, since: datetime, limit: int) -> list[dict]:
    criteria = f'(FROM "{sender}" SINCE "{since.strftime("%d-%b-%Y")}")'
    status, data = client.search(None, criteria)
    if status != "OK":
        raise RuntimeError(f"搜索发件人失败：{sender}: {status}")
    ids = data[0].split()
    results: list[dict] = []
    for raw_id in ids[-limit:]:
        status, payload = client.fetch(raw_id, "(RFC822)")
        if status != "OK" or not payload or payload[0] is None:
            print(f"警告：读取邮件失败，跳过 uid={raw_id.decode()}（{sender}）", file=sys.stderr)
            continue
        message = email.message_from_bytes(payload[0][1], policy=email.policy.default)
        results.append(normalize(message))
    return results


def load_senders(args: argparse.Namespace) -> list[str]:
    senders: list[str] = []
    if args.senders:
        senders += [part.strip() for part in args.senders.split(",")]
    if args.senders_file:
        senders += [
            line.strip()
            for line in args.senders_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    senders = sorted({sender.lower() for sender in senders if sender})
    if not senders:
        raise ValueError("请通过 --senders 或 --senders-file 提供至少一个发件人白名单地址")
    return senders


def dedupe_emails(emails: list[dict]) -> list[dict]:
    """合并多个发件人查询重复命中的同一封邮件。"""
    unique: dict[tuple[str, ...], dict] = {}
    for item in emails:
        message_id = item.get("message_id", "").strip().lower()
        key = (
            ("message_id", message_id)
            if message_id
            else (
                "fallback",
                item.get("from_address", ""),
                item.get("subject", ""),
                item.get("published_at", ""),
            )
        )
        unique.setdefault(key, item)
    return list(unique.values())


def fetch_mail_drop(
    address: str,
    password: str,
    senders: list[str],
    since: datetime,
    max_per_sender: int,
) -> dict:
    """读取、去重并按精确时间过滤邮件，供 CLI 与定时任务共用。"""
    client = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        client.login(address, password)
        client.select("INBOX", readonly=True)
        emails: list[dict] = []
        for sender in senders:
            fetched = fetch_from_sender(client, sender, since, max_per_sender)
            print(f"{sender}: {len(fetched)} 封")
            emails.extend(fetched)
    finally:
        try:
            client.logout()
        except OSError:
            pass

    raw_count = len(emails)
    emails = dedupe_emails(emails)
    if len(emails) != raw_count:
        print(f"去重：{raw_count} → {len(emails)} 封")

    precise: list[dict] = []
    for item in emails:
        try:
            published = datetime.fromisoformat(item["published_at"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            precise.append(item)
            continue
        if published.astimezone(timezone.utc) >= since.astimezone(timezone.utc):
            precise.append(item)
    precise.sort(key=lambda item: item["published_at"], reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "account": address,
        "senders": senders,
        "since": since.astimezone(timezone.utc).isoformat(),
        "emails": precise,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="按发件人白名单拉取 Gmail 邮件")
    parser.add_argument("--senders", help="逗号分隔的发件人地址")
    parser.add_argument("--senders-file", type=Path, help="发件人白名单文件，每行一个地址，# 开头为注释")
    parser.add_argument("--days", type=int, default=7, help="拉取最近 N 天（默认 7）")
    parser.add_argument("--max-per-sender", type=int, default=10, help="每个发件人最多拉取封数（默认 10）")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    address = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not address or not password:
        raise RuntimeError("缺少环境变量 GMAIL_ADDRESS / GMAIL_APP_PASSWORD（请使用 Google 应用专用密码）")

    senders = load_senders(args)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    drop = fetch_mail_drop(address, password, senders, since, args.max_per_sender)
    today = datetime.now().strftime("%Y-%m-%d")
    drop["days"] = args.days
    output = args.output_root / f"{today}-mail-drop.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(drop, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"共 {len(drop['emails'])} 封邮件已写入：{output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, imaplib.IMAP4.error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
