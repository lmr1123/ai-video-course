/* 重讲课分镜数据 · 来源：examples/课程样例-openai-codex-访谈.md
 * 每个镜头 = 一个教学意图。narration 即 TTS 输入；source 为原片时间戳（秒），可点击跳回原片核对。
 * type: card 旁白卡 | quote 引用卡 | diagram 机制图 | compare 对比卡 | case 案例白板 | check 章节检查
 */
window.STORYBOARD = {
  title: "当“实现”变得廉价，产品工作会倒转成什么样",
  origin: { video: "P3KDebPTUrw", label: "Lenny's Podcast × Andrew Ambrosino" },
  acts: ["开场", "核心模型：流程倒转", "模型时机", "角色与收尾"],
  shots: [
  { act: 0, type: "card", title: "钩子",
    narration: "你大概有过这样的时刻：让 AI 十分钟做出一个功能原型，兴奋地发给别人看，得到的反馈却全是按钮太丑了、配色不好看。方向层面的问题，反而没人讨论。今天这节课要讲的，就是这个现象背后的大变化：当实现变得廉价，产品工作正在整个倒过来。",
    visual: { kicker: "重讲课 · 精讲版", lines: ["当“实现”变得廉价", "产品工作会倒转成什么样"] },
    source: [], attribution: "解释" },

  { act: 0, type: "card", title: "学习承诺",
    narration: "这节课的内容，来自 OpenAI Codex 桌面应用负责人 Andrew Ambrosino 的访谈。学完之后，你应该能回答三个问题：第一，产品流程为什么倒转了；第二，功能效果不好，该砍掉还是该等；第三，角色分工到底还有没有意义。",
    visual: { kicker: "学完你能回答", lines: ["① 流程为什么倒转了", "② 功能失败：该砍，还是该等", "③ 角色分工还有意义吗"] },
    source: [178], attribution: "解释" },

  { act: 1, type: "diagram", title: "旧世界流水线",
    narration: "先看旧世界。研究、写文档、做设计、出原型、最后实现。这条流水线的排序逻辑只有一个字：贵。实现最贵，所以前面所有环节，都是为了在动工之前把风险降到最低。",
    visual: { kind: "flow", steps: ["研究", "文档", "设计", "原型", "实现"], highlight: 4, note: "实现最贵 → 一切为动工前降险" },
    source: [178], attribution: "归纳" },

  { act: 1, type: "quote", title: "倒转",
    narration: "现在，整个反过来了。用嘉宾的原话说：一切都倒转了。实现不再是贵的那部分，贵的是——品味。在 OpenAI 内部，同一个需求，可能同时存在九十个互不知情的原型。",
    visual: { en: "“It's backwards. The implementation is actually not the expensive part anymore. It's, dare I say, taste.”", who: "Andrew Ambrosino" },
    source: [303, 232], attribution: "原话" },

  { act: 1, type: "diagram", title: "新世界",
    narration: "流程倒转之后，稀缺的能力从做出来，变成了策展：从九十个尝试里判断什么是好的、哪些该合并、该用什么方式呈现。做出来人人都会，判断值不值得做、往哪个方向收敛，成了新的瓶颈。",
    visual: { kind: "flow", steps: ["人人实现 ×90", "策展与判断", "收敛发布"], highlight: 1, note: "稀缺资源：taste（品味与判断）" },
    source: [232], attribution: "归纳" },

  { act: 1, type: "compare", title: "精致度不再是进度",
    narration: "还有一个更隐蔽的变化：外观不再携带进度信息。过去你看到一个接近成品的界面，就知道它走完了全部评审，因为不评审就拿不到开发资源。现在，一个看起来马上能上线的原型，可能只是某个人昨晚的即兴试验。所以团队必须显式声明：我们现在处在流程的哪一步。",
    visual: { left: { title: "旧世界：精致度 = 进度", items: ["设计已过审", "假设已验证", "可以准备上线"] },
              right: { title: "新世界：精致度 ≠ 进度", items: ["可能只是昨晚的探索", "必须显式标注阶段", "否则团队被错误锚定"] } },
    source: [544], attribution: "归纳" },

  { act: 1, type: "case", title: "PRD 没死",
    narration: "顺便说一句，嘉宾明确反对 PRD 已死的说法。要澄清模糊的产品方向，文档仍然是对的媒介；要压测交互细节，才轮到原型。选择媒介的本质，是选择你想让团队对什么做出反应。原型是很强的第一笔，它会把讨论从该解决什么问题，拉到按钮该放在哪。",
    visual: { title: "媒介要匹配目的", steps: ["澄清模糊方向 → 写文档", "压测交互模式 → 出原型", "原型是很强的“第一笔”：它决定大家讨论什么"] },
    source: [438, 495], attribution: "归纳" },

  { act: 1, type: "check", title: "章节检查",
    narration: "到这里停一下。请用你自己的话回答：为什么说一个精致的原型，反而可能是个陷阱？想清楚了，点继续。",
    visual: { question: "为什么“精致的原型”反而可能是陷阱？" },
    source: [495], attribution: "解释" },

  { act: 2, type: "quote", title: "十一月 vs 二月",
    narration: "第二个核心判断，关于时机。嘉宾说了一句非常确定的话：二月发布的 Codex 应用，如果十一月就绪，会在市场上彻底失败。唯一的区别，是模型。同一个产品形态，成败完全取决于模型够不够聪明。",
    visual: { en: "“If that had been ready in November, it would have absolutely failed. The only difference was the models.”", who: "Andrew Ambrosino" },
    source: [2011], attribution: "原话" },

  { act: 2, type: "diagram", title: "原型组合循环",
    narration: "所以规划方式变了：把想做的功能全部做成原型；就绪的发布；不就绪的不算失败，算资产，先搁置；每次模型跃迁，把整个组合重新试一遍。同一个功能，可能要发布六次，才等到属于它的时刻。",
    visual: { kind: "cycle", steps: ["全部原型化", "就绪 → 发布", "未就绪 → 搁置为资产", "模型跃迁 → 全部重试"], note: "同一形态可能要发布六次" },
    source: [1949, 2253], attribution: "归纳" },

  { act: 2, type: "case", title: "too AGI-pilled",
    narration: "反面教材是初版的 Codex 网页版：纯委托式，你把任务丢给它，它自己干完回来交差。方向听起来没错，但当时的模型撑不起来。嘉宾的总结是：我们当时太相信通用智能已经到位了。同期的 Claude Code 完全本地运行、会不停问你问题、不假装能全自主，反而赢了——因为那才是当时模型的真实水位。",
    visual: { title: "同一水位，两种命运", steps: ["Codex web：纯委托，形态超前 → 失败", "Claude Code：本地、会提问、不装全知 → 成功", "差别不在想法，在对模型真实水位的判断"] },
    source: [2228], attribution: "归纳" },

  { act: 2, type: "check", title: "章节检查",
    narration: "再停一下。想一个你过去一年放弃的 AI 功能：当时失败的原因，是形态不对，还是模型没到位？如果是后者，它应该进入你的重试清单。",
    visual: { question: "你放弃过的那个 AI 功能——是形态不对，还是模型没到位？" },
    source: [2253], attribution: "解释" },

  { act: 3, type: "diagram", title: "角色塌缩",
    narration: "第三个判断，关于角色。角色没有消失，而是塌缩了。以前，角色是格子间，设计做到这堵墙为止，工程从那堵墙开始。现在是散点图：每个人的工作散落在多个学科，你的角色，是所有这些点的平均位置。设计师也写代码，但均值仍然落在设计上。",
    visual: { kind: "scatter", note: "角色 = 工作内容的平均位置（质心）" },
    source: [1316], attribution: "归纳" },

  { act: 3, type: "quote", title: "围栏塌了，学科没塌",
    narration: "但嘉宾同时给出了全片最直接的警告：取消产品角色，是个糟糕透顶的主意。因为角色背后是学科，是几十年积累的最佳实践。他的比喻是：你会用 Excel，不等于你能进财务团队。围栏塌了，学科没塌。",
    visual: { en: "“Yes, you can use Excel, but you cannot work on the finance team.”", who: "Andrew Ambrosino" },
    source: [1471, 1556], attribution: "原话" },

  { act: 3, type: "compare", title: "取舍与边界",
    narration: "最后说边界：这套判断什么时候不适用。如果你的产品根本不依赖模型能力，那等潮水就是借口，不是策略。如果团队没有人做策展和判断，九十个原型就只是九十份垃圾。流程倒转的前提，是实现真的廉价，而且有人负责品味。",
    visual: { left: { title: "适用", items: ["产品依赖模型能力", "有人做策展与判断", "重发的边际成本低"] },
              right: { title: "不适用", items: ["与模型无关的产品", "没人负责收敛方向", "每次重发都很贵"] } },
    source: [2282], attribution: "解释" },

  { act: 3, type: "card", title: "最小实践",
    narration: "给你一个十五分钟的最小实践。翻出最近一个用 AI 快速做出来的功能，写三行字：第一，它当时要验证什么问题；第二，直接上原型，让你提前锚定了哪个本来可以再讨论的决定；第三，如果重来，哪一步应该先写半页文档。",
    visual: { kicker: "最小实践 · 15 分钟", lines: ["① 它要验证什么问题", "② 原型让你提前锚定了哪个决定", "③ 重来的话，哪一步先写半页文档"] },
    source: [464], attribution: "解释" },

  { act: 3, type: "card", title: "三句话复盘",
    narration: "三句话复盘。第一，实现廉价之后，贵的是策展和判断。第二，功能失败，先问模型时机；不就绪的功能是资产，不是失败。第三，角色是你工作的平均位置；围栏塌了，专业没塌。这节课的每个结论，都可以点击屏幕下方的时间戳，回到原片核对。我们下节课见。",
    visual: { kicker: "三句话复盘", lines: ["实现廉价 → 贵的是策展和判断", "功能失败 → 先问模型时机", "角色 = 平均位置；围栏塌了，专业没塌"] },
    source: [303, 2011, 1316], attribution: "归纳" }
]};
