#!/usr/bin/env python3
"""资讯速听页的本地 Chromium 冒烟回归。需先启动 tools/serve_local.py。"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:8741/prototype/briefing/"
FIXTURE = ROOT / "tests" / "fixtures" / "briefing.json"


def check_viewport(browser, width: int, height: int, screenshot: str) -> None:
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""
      window.__audioSources = [];
      window.__speechCalls = 0;
      window.Audio = class {
        constructor(src) { this.src = src; window.__audioSources.push(src); }
        play() { setTimeout(() => this.onended?.(), 90); return Promise.resolve(); }
        pause() {}
      };
      window.addEventListener('DOMContentLoaded', () => {
        window.speechSynthesis.cancel = () => {};
        window.speechSynthesis.speak = utterance => {
          window.__speechCalls += 1;
          setTimeout(() => utterance.onend?.(), 90);
        };
      });
    """)
    page.goto(URL, wait_until="networkidle")
    page.wait_for_selector("#queue .queue-item")

    assert page.locator("#queue .queue-item").count() == 4
    assert page.locator("#script .segment").count() == 3
    assert page.locator("#position").inner_text() == "01 / 04"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    page.locator("#save").click()
    assert page.locator("#save").inner_text() == "已收藏"
    assert "item-001" in page.evaluate("localStorage.getItem('briefing:saved')")

    page.locator("#queue [data-index='2']").click()
    assert page.locator("#position").inner_text() == "03 / 04"
    assert "NotebookLM" in page.locator("#item-title").inner_text()

    page.locator("#play").click()
    page.wait_for_timeout(140)
    page.locator("#play").click()
    assert page.locator("#progress").evaluate("node => parseFloat(node.style.width) > 0")
    assert page.evaluate("window.__speechCalls") == 0
    assert page.evaluate("window.__audioSources[0].endsWith('audio/item-003-segment-01.mp3')")

    audio_statuses = page.evaluate("""
      async () => {
        const batch = await (await fetch('./briefing.json')).json();
        return Promise.all(batch.items.flatMap(item => item.spoken_segments).map(async segment => ({
          path: segment.audio_file,
          status: (await fetch(segment.audio_file)).status
        })));
      }
    """)
    assert len(audio_statuses) == 12
    assert all(entry["path"] and entry["status"] == 200 for entry in audio_statuses)

    page.set_input_files("#file", str(FIXTURE))
    page.wait_for_timeout(100)
    assert page.locator("#batch-title").inner_text() == "多源资讯速听 · 市场扫描"
    assert page.locator("#original").get_attribute("href").startswith("https://")
    assert page.locator("#deepen").is_visible()
    assert page.locator("#transport").is_visible()
    assert page.locator("#live").evaluate("node => node.getBoundingClientRect().width <= 1")
    assert not errors, errors

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)
    page.screenshot(path=screenshot)
    context.close()


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        check_viewport(browser, 1280, 900, "/tmp/briefing-desktop.png")
        check_viewport(browser, 390, 844, "/tmp/briefing-mobile.png")
        browser.close()
    print("资讯速听浏览器回归通过：桌面 1280×900，手机 390×844")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"浏览器回归失败：{exc}", file=sys.stderr)
        raise
