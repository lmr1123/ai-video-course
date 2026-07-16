#!/usr/bin/env python3
"""为 macOS 安装可配置的每日 Gmail 资讯速听任务。"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.mail_briefing_job import DEFAULT_CONFIG, DEFAULT_ENV, load_config


LABEL = "com.ai-video-course.mail-briefing"


def build_plist(config_path: Path, env_path: Path, python: Path) -> dict:
    config = load_config(config_path)
    logs = ROOT / "local-data" / "briefing" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            str(ROOT / "tools" / "mail_briefing_job.py"),
            "--config",
            str(config_path.resolve()),
            "--env",
            str(env_path.resolve()),
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "TZ": config.schedule.timezone,
        },
        "StartCalendarInterval": {
            "Hour": config.schedule.hour,
            "Minute": config.schedule.minute,
        },
        "StandardOutPath": str(logs / "mail-briefing.log"),
        "StandardErrorPath": str(logs / "mail-briefing.error.log"),
        "ProcessType": "Background",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="安装 Gmail 资讯速听 macOS 定时任务")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    destination = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if args.uninstall:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], check=False)
        destination.unlink(missing_ok=True)
        print(f"已卸载：{destination}")
        return

    value = build_plist(args.config, args.env, args.python)
    payload = plistlib.dumps(value, sort_keys=False)
    if args.dry_run:
        sys.stdout.buffer.write(payload)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(destination)], check=False)
    subprocess.run(["launchctl", "bootstrap", domain, str(destination)], check=True)
    print(
        f"已安装：每天 {value['StartCalendarInterval']['Hour']:02d}:"
        f"{value['StartCalendarInterval']['Minute']:02d} 运行（{destination}）"
    )


if __name__ == "__main__":
    main()
