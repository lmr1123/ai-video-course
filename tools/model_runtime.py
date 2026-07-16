"""模型调用的内容指纹、用量归一化和本地账本工具。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def estimate_tokens_from_chars(characters: int) -> int:
    """对中英文混合输入使用保守的 2 字符/Token 预估。"""
    return (max(0, characters) + 1) // 2


def normalize_usage(value: Any) -> dict[str, int]:
    if value is None:
        raw: dict[str, Any] = {}
    elif hasattr(value, "model_dump"):
        raw = value.model_dump()
    elif isinstance(value, dict):
        raw = value
    else:
        raw = {}
    input_tokens = int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0)
    output_tokens = int(raw.get("output_tokens") or raw.get("completion_tokens") or 0)
    total_tokens = int(raw.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def new_ledger(pipeline: str, model: str, prompt_version: str) -> dict[str, Any]:
    return {
        "pipeline": pipeline,
        "model": model,
        "prompt_version": prompt_version,
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "status": "running",
        "estimated": {},
        "cache_hits": 0,
        "cache_misses": 0,
        "skipped": [],
        "attempts": [],
        "totals": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def add_attempt(
    ledger: dict[str, Any],
    *,
    unit: str,
    attempt: int,
    status: str,
    usage: Any = None,
    error: str | None = None,
) -> None:
    normalized = normalize_usage(usage)
    entry: dict[str, Any] = {
        "unit": unit,
        "attempt": attempt,
        "status": status,
        "usage": normalized,
    }
    if error:
        entry["error"] = error
    ledger["attempts"].append(entry)
    for key, value in normalized.items():
        ledger["totals"][key] += value


def finish_ledger(ledger: dict[str, Any], status: str, error: str | None = None) -> None:
    ledger["status"] = status
    ledger["finished_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    if error:
        ledger["error"] = error


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)
