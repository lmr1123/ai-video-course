#!/usr/bin/env python3
"""从分镜数据生成旁白音频（edge-tts，免费）。

用法：python3 tools/tts_generate.py [--voice zh-CN-YunxiNeural] [--rate -4%]
输出：prototype/relecture/audio/shot-NN.mp3 + manifest.json（时长，供导出器用）
"""
import argparse, asyncio, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB_PATH = ROOT / "prototype/relecture/storyboard.js"
OUT_DIR = ROOT / "prototype/relecture/audio"


def load_narrations():
    """storyboard.js 是 JS 文件；narration 值为单行双引号字符串，直接正则提取。"""
    text = SB_PATH.read_text(encoding="utf-8")
    narrations = re.findall(r'narration:\s*"([^"]*)"', text)
    if not narrations:
        sys.exit("storyboard.js 中未找到 narration 字段")
    return narrations


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return round(float(out.stdout.strip()), 3)


async def synth(narrations, voice, rate):
    import edge_tts
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, text in enumerate(narrations, 1):
        mp3 = OUT_DIR / f"shot-{i:02d}.mp3"
        await edge_tts.Communicate(text, voice, rate=rate).save(str(mp3))
        dur = probe_duration(mp3)
        manifest.append({"shot": i, "file": mp3.name, "duration": dur, "chars": len(text)})
        print(f"shot-{i:02d}  {dur:6.1f}s  {len(text)} 字")
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(m["duration"] for m in manifest)
    print(f"\n共 {len(manifest)} 镜，总时长 {total/60:.1f} 分钟 → {OUT_DIR}/manifest.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="zh-CN-YunxiNeural")
    ap.add_argument("--rate", default="-4%")
    args = ap.parse_args()
    asyncio.run(synth(load_narrations(), args.voice, args.rate))
