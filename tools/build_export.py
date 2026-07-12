#!/usr/bin/env python3
"""重讲课 MP4 导出器。

流程：Playwright 逐镜截图（1920×1080，render 模式）→
  1) 生成 HyperFrames composition 工程（export/hyperframes/，可用 `npx hyperframes render` 渲染）
  2) ffmpeg 直接合成 export/relecture.mp4（零依赖兜底，与 HyperFrames 同一套素材）

前置：已运行 tools/tts_generate.py；本地服务器在 8737（python3 -m http.server 8737 --directory prototype）。
用法：python3 tools/build_export.py [--base http://localhost:8737] [--chromium <path>]
"""
import argparse, json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "prototype/relecture/audio"
EXPORT = ROOT / "export"
HF_DIR = EXPORT / "hyperframes"
GAP = 0.35  # 镜头间静默间隔（秒）


def load_manifest():
    mf = AUDIO_DIR / "manifest.json"
    if not mf.exists():
        sys.exit("缺少 audio/manifest.json，请先运行 tools/tts_generate.py")
    return json.loads(mf.read_text(encoding="utf-8"))


def screenshot_shots(base, n, chromium):
    from playwright.sync_api import sync_playwright
    frames = EXPORT / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        kw = {"executable_path": chromium} if chromium else {}
        b = p.chromium.launch(**kw)
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        for i in range(n):
            pg.goto(f"{base}/relecture/?render=1&shot={i}", wait_until="networkidle")
            pg.wait_for_timeout(400)  # 字体渲染
            pg.locator(".canvas").screenshot(path=str(frames / f"shot-{i+1:02d}.png"))
            print(f"截图 shot-{i+1:02d}")
        b.close()
    return frames


def build_hyperframes(manifest):
    """生成 HyperFrames composition：<img>+<audio> clip 时间轴（官方 HTML schema）。"""
    assets = HF_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    t, clips = 0.0, []
    for m in manifest:
        i, dur = m["shot"], m["duration"]
        shutil.copy(EXPORT / "frames" / f"shot-{i:02d}.png", assets / f"shot-{i:02d}.png")
        shutil.copy(AUDIO_DIR / m["file"], assets / m["file"])
        clips.append(
            f'  <img id="img-{i}" class="clip" data-start="{t:.2f}" data-duration="{dur+GAP:.2f}" '
            f'data-track-index="1" src="./assets/shot-{i:02d}.png" />\n'
            f'  <audio id="au-{i}" data-start="{t:.2f}" data-track-index="2" src="./assets/{m["file"]}"></audio>')
        t += dur + GAP
    html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>relecture</title>
<style>
  [data-composition-id="root"]{{background:#f7f2e9}}
  img.clip{{position:absolute;inset:0;width:1920px;height:1080px;object-fit:cover}}
</style></head>
<body>
<div id="root" data-composition-id="root" data-start="0" data-width="1920" data-height="1080">
{chr(10).join(clips)}
</div>
</body></html>
'''
    (HF_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"HyperFrames 工程 → {HF_DIR}（总时长 {t/60:.1f} 分钟）")
    print("  渲染：cd export/hyperframes && npx hyperframes render -o ../relecture-hf.mp4")


def build_ffmpeg(manifest):
    """ffmpeg 兜底直渲：每镜 图+音 → 分段 → concat。"""
    segs_dir = EXPORT / "segs"
    segs_dir.mkdir(parents=True, exist_ok=True)
    seg_files = []
    for m in manifest:
        i, dur = m["shot"], m["duration"] + GAP
        png = EXPORT / "frames" / f"shot-{i:02d}.png"
        mp3 = AUDIO_DIR / m["file"]
        seg = segs_dir / f"seg-{i:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(png), "-i", str(mp3),
            "-t", f"{dur:.2f}", "-r", "30",
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-af", f"apad=whole_dur={dur:.2f}",
            str(seg)], check=True)
        seg_files.append(seg)
        print(f"分段 seg-{i:02d} ({dur:.1f}s)")
    lst = segs_dir / "list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in seg_files), encoding="utf-8")
    out = EXPORT / "relecture.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(out)], check=True, cwd=segs_dir)
    dur = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    print(f"成片 → {out}（{float(dur)/60:.1f} 分钟）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8737")
    ap.add_argument("--chromium", default=None, help="Playwright Chromium 可执行文件路径（可选）")
    args = ap.parse_args()
    manifest = load_manifest()
    screenshot_shots(args.base, len(manifest), args.chromium)
    build_hyperframes(manifest)
    build_ffmpeg(manifest)
