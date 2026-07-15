# AI Video Course

把 YouTube 上较长、偏技术的 AI Engineering 视频，重构成适合 vibe coding 开发者进阶的结构化课程。

产品定位、目标用户、学习流程和功能边界以 [docs/需求与方案.md](docs/需求与方案.md) 为唯一事实来源，本文件不重复展开。

## 当前阶段

需求与学习流程验证阶段。首个真实视频课程、互动课程页和 AI 教师重讲课原型已经跑通，进展见 [tasks/progress.md](tasks/progress.md)。

## 在线体验

- [互动课程页](https://lmr1123.github.io/ai-video-course/)
- [AI 教师重讲课](https://lmr1123.github.io/ai-video-course/relecture/)

页面针对手机宽度做了响应式适配。GitHub Pages 在 `main` 分支的 `prototype/` 发生变化时自动更新。

## 目录

```text
docs/需求与方案.md       产品定位、用户、流程、MVP 与成功指标（唯一 PRD）
docs/课程编排与呈现.md   内容筛选、成本控制、课程形态（各节标注验证状态）
docs/重讲模式方案.md     AI 教师重讲模式：路线选型、分镜 schema、导出层
docs/多源资讯速听模式方案.md  多源短资讯、连续语音、溯源与验证方案
docs/决策日志.md         关键选择、理由与待验证假设
examples/                课程样例（真实视频黄金测试产物）与结构模板
experiments/             对照实验（NotebookLM 基线等）
prototype/               互动课程页原型（index.html）＋重讲课播放页（relecture/）
tools/                   tts_generate.py（旁白合成）、build_export.py（MP4 导出）
tasks/progress.md        任务、实验和当前进度
tasks/lessons.md         用户纠正、踩坑与可复用教训
AGENTS.md                AI 协作规则
```

## 运行原型

```bash
python3 -m http.server 8737 --directory prototype   # → http://localhost:8737
python3 tools/tts_generate.py                        # 生成重讲课旁白（edge-tts，免费）
python3 tools/build_export.py                        # 分镜截图 + HyperFrames 工程 + ffmpeg 成片
```

依赖：Python 3 + `pip install edge-tts playwright`、ffmpeg；MP4 的 HyperFrames 渲染路线另需 Node ≥ 22（`npx hyperframes render`）。

## 本地资讯速听

首轮原型使用人工整理的内容包验证“短卡提炼、句级溯源和连续播放”，暂不接 Gmail、RSS 或 X 账号：

```bash
python3 tools/briefing_pipeline.py \
  --fixture tests/fixtures/briefing.json \
  --output-root /tmp/briefing-output

python3 tools/serve_local.py --port 8737
# → http://localhost:8737/prototype/briefing/
```

页面默认加载仓库内的市场扫描样例及固定音频；也可以点击“导入内容包”选择本地 `briefing.json`。默认播报使用 `zh-CN-XiaoxiaoNeural / +4% / -2Hz` 的暖声线，避免不同手机的系统语音产生机械音。导入内容没有预生成 MP3 时才临时降级到浏览器中文语音，并在播放状态中明确提示。需要为本地内容生成并缓存 MP3 时运行：

```bash
python3 tools/briefing_pipeline.py \
  --fixture tests/fixtures/briefing.json \
  --tts --serve
```

真实内容包和生成音频写入 `local-data/briefing/`，不会被 Git 跟踪或发布。正式范围、内容分型和验证协议见 [多源资讯速听模式方案](docs/多源资讯速听模式方案.md)。20 条首轮素材与裸读基线模板见 [资讯速听首轮验证](experiments/资讯速听首轮验证/素材与基线.md)。

## 自动生成课程

自动管线在本地运行，API 密钥不会进入网页或仓库：

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="你的密钥"
python3 tools/course_pipeline.py "https://youtu.be/VIDEO_ID"
```

流程：`YouTube URL → yt-dlp 英文字幕与元数据 → OpenAI Responses API 结构化课程 → 时间戳/schema 校验 → prototype/generated/`。默认模型为 `gpt-5.4`，可用 `OPENAI_MODEL` 或 `--model` 覆盖。

生成成功后，本地打开 `http://localhost:8737/generated/viewer.html?id=VIDEO_ID`。提交 `prototype/generated/` 后，GitHub Pages 会自动发布；首页输入框也会通过 `manifest.json` 识别已生成课程。

不调用 API 的离线回归：

```bash
python3 tools/course_pipeline.py --fixture tests/fixtures/course.json --output-root /tmp/course-output
python3 -m unittest tests/test_course_pipeline.py -v
```

## 本地中文播放

用户克隆项目后，可以在自己的电脑上为第三方视频生成中文字幕与同步中文音轨，并通过本地课程页播放。字幕、译文、音轨和 API 密钥只保存在用户本地，不进入 Git、不上传 GitHub Pages，也不依赖项目方服务器。

首次验证建议只生成连续 10—20 分钟：

```bash
pip install -r requirements.txt
brew install yt-dlp ffmpeg                    # macOS；其他系统安装同名工具
export OPENAI_API_KEY="你的密钥"

python3 tools/dubbing_pipeline.py \
  "https://youtu.be/P3KDebPTUrw" \
  --start 00:02:58 \
  --minutes 10 \
  --serve
```

流程：`YouTube URL → 英文短字幕 → OpenAI 中文口语翻译 → edge-tts 分句配音 → local-data/<video-id>/ → 本地同步播放器`。访谈默认用同一条自然声线区分角色：主持人 `Yunxi / -4Hz / +2%`，嘉宾 `Yunxi / -10Hz / -2%`；可通过 `--host-voice`、`--host-pitch`、`--host-rate` 及对应的 `--guest-*` 参数覆盖。`--serve` 只监听 `127.0.0.1`；按 `Ctrl+C` 停止。播放器支持中文/原声切换、核对原话、字幕开关、变速、拖动和时间线跳转。

重新执行同一片段会复用已翻译的 manifest 和未变化的 MP3；角色或声音参数变化时会重生成音频。单条 TTS 遇到临时网络错误会有限重试，并只在新音频完整生成后替换旧缓存。音频超过原时间窗 1.35 倍时 CLI 会明确警告，需要压缩对应译文。只生成字幕和中文稿可加 `--skip-tts`。产品与产物边界见 [docs/中文播放模式方案.md](docs/中文播放模式方案.md)。

开源代码不等于自动获得第三方内容的再分发权，“个人技术分享”或“非商用”本身也不足以保证可以把合成音轨提交到公共仓库。默认只提交自有、明确授权或许可允许衍生和再分发的音轨；普通第三方 YouTube 视频生成的字幕和中文音轨留在 `local-data/`。参考 [YouTube 服务条款](https://www.youtube.com/static?template=terms)、[版权与衍生作品说明](https://support.google.com/youtube/answer/2797466)、[合理使用说明](https://support.google.com/youtube/answer/9783148) 与 [CC BY 说明](https://support.google.com/youtube/answer/2797468)。
