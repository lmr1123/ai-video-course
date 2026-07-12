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
