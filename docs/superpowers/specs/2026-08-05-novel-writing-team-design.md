# 小说撰写团队设计方案

- 日期: 2026-08-05
- 状态: 待实施
- 目标形态: 6 个 Agent 组成的中文网文撰写流水线，纯 Agent 编排，零新增插件代码

---

## 1. 目标与范围

在 VCPToolBox 内搭建一支能协作完成中文网文创作的 Agent 团队，覆盖从"一句话设定"到"逐章成稿"的全流程，并在长篇场景下保持设定、人设、时间线与伏笔的一致性。

### 1.1 已确定的范围决策

| 维度 | 决策 |
|------|------|
| 体量 | 先按中篇（10–30 万字 / 20–50 章）跑通，架构为长篇（100+ 章）预留 |
| 题材 | 中文网文（重爽感密度与章末钩子），题材作为可插拔 preset |
| 人工介入 | review 档：设定确认 1 次 + 每章验收 N 次 + 弧末方向确认 N/5 次 |
| 编排方式 | 纯 Agent 编排，复用 `AgentAssistant` 的异步委托与链式交接，不新增编排引擎 |
| 编制 | 6 个 Agent，每章常规 3 道工序 |

### 1.2 明确不做（YAGNI）

- 不新建工作流引擎、状态机框架或任务队列。
- 不引入 LangChain / CrewAI / AutoGen。调研的六个头部项目一概不用。
- 不做 Web UI。进度可见性由现有 `TopicSponsor` 话题机制承担。
- 不做并行扇出审计。项目约束不允许（见 3.1）。
- 阶段一不新增插件代码。确定性校验复用现有 `WeWriteHumanness`。

---

## 2. 调研结论

### 2.1 参考项目

| 项目 | Star | 采纳的做法 |
|---|---|---|
| voocel/ainovel-cli | 1547 | 卷/弧双层滚动规划（指南针 Compass）；三级摘要（卷→弧→章）；五种弧型节奏模板；按角色配模型 |
| Mochocyang/QMAI 青幕AI | 621 | 上下文包按固定优先级组装；角色认知系统（knows / does_not_know）；伏笔债务评分；草稿隔离 |
| papysans/Morpheus | 35 | 运行态派生记忆（OPEN_THREADS） |
| mrigankad/Novel-OS | 30 | 确定性连续性检查先跑，再让 LLM 复核 |
| tiny-flowlab/novel-studio-copilot-cli | 19 | 纯 prompt 文件驱动的 Agent 定义；三档人工介入模式 |
| CalWade/novelforge | 1 | 黑板模式（文件即唯一共享记忆）；对抗式审稿默认拒稿；每 Agent 独立温度；数据隔离边界；带病上线防死循环 |

### 2.2 六项跨项目共识

这些是设计的硬约束，不是可选项：

1. **状态沉盘，不靠上下文窗口。** 每个 Agent 每次调用都是全新会话，只读它需要的 1–2 个文件。进程死了读文件即可续跑。
2. **写手与审稿必须是不同 Agent，且审稿默认拒稿。** 自评永远过于乐观。
3. **不一次性规划全书。** 长篇用"指南针 + 视野"滚动展开，写到哪展开到哪。
4. **一致性靠结构化台账，不靠模型记性。** 状态卡、伏笔池、时间线、角色认知四本账，每章由专职 Agent 覆盖式刷新。
5. **规则按需披露。** 入口只做目录页，详细规则拆小文件，每个 Agent 只加载自己那 1–2 份。
6. **不用 Agent 框架。** 一个串行循环 + 一张职责表足够。

---

## 3. 项目现状与约束

### 3.1 从 `Agent/Helm.txt` 继承的运行铁律

`Agent/Helm.txt` 是仓库内已跑通的同构团队（公众号撰写：Helm 主编 → Sage 调研 → Loom 拆题 → Quill 撰写）。它记录的教训标注为"踩过的真实事故"，小说团队必须原样继承：

| 铁律 | 原因 |
|---|---|
| **一次只发起一个委派**，全队最多 1 个在途委托 | 历史上并发 4 个调研任务打爆过模型服务 |
| **异步不轮询**：委派后嵌 `{{VCP_ASYNC_RESULT::AgentAssistant::<id>}}` 占位符立即松手 | `query_delegation` 轮询会让主控空转刷轮次直到卡死 |
| **跨 Agent 只传短文本与路径**，长产物落盘后传绝对路径 + `inject_tools` 注入读取工具 | 长文本会被截断、爆上下文 |
| **一律用 `ServerFileOperator`，严禁 `FileOperator`** | 后者路由到桌面分布式节点，无权访问 `E:\VCPChat\VCPToolBox`，且会命中人工审核卡死 |
| **总编必须独立 `FileInfo` 二次验证**下游报的路径 | 下游自验会因幻觉失效，这是管道的最后防线 |
| **`callback_on: always` 时先读回执的 `状态:` 字段**，不看事件名 | 失败也会回呼，事件名里的"COMPLETE"只是标签 |
| **同一工具连续失败 2 次即停手**，向用户如实汇报 | 相同参数只会同样地失败，重试是空转 |
| `ListDirectory` / `CreateDirectory` 用 `directoryPath`，不是 `filePath` | 传错会报 `paths[0] must be of type string` |

补充约束：`AgentAssistant` 对同一 Agent 的持久会话有占线锁（`activeSessionLocks`），正在通讯时会拒接。这与"串行"设计一致，但意味着不能同时给同一 Agent 派两个活。

### 3.2 已有的全局委托纪律（不需在 Agent 定义中重复）

`Plugin/AgentAssistant/config.json` 的 `delegationSystemPrompt` 已全局注入所有委托任务，内容包括：

- 真实结果优先，严禁伪造工具返回（不得预写"操作成功""文件已写入""验证通过"）。
- 验证容错：`WriteFile` 后 `FileInfo` 异常时先重试、必要时改用 `ReadFile` 复核同一路径，不因单次抖动判定失败。
- 谨慎判定失败：核心产物已交付时不得误报 `[[TaskFailed]]`。
- `ServerFileOperator` 铁律与 Windows 绝对路径要求。
- `[[NextHeartbeat::秒数]]` 长任务延时机制，`[[TaskComplete]]` / `[[TaskFailed]]` 收尾协议。

**六个 Agent 的定义文件不要重复这些内容**，只写各自的职责、读写边界与产物规范。

### 3.3 Agent 注册机制

两套独立命名空间，通过占位符串联：

1. `Plugin/AgentAssistant/config.json` 的 `agents[]` 数组 —— 决定 `agent_name` 能调用到谁。字段：`baseName`（ASCII 基名，用于积分与 session key）、`chineseName`（**调用名**）、`modelId`、`systemPrompt`、`maxOutputTokens`、`temperature`、`description`。
2. `agent_map.json` 的 `别名: 文件名` 映射 —— 决定 `{{别名}}` 占位符解析到 `Agent/` 下哪个文件。`modules/agentManager.js` 负责读取并热监听变更。

串联方式：`config.json` 里写 `"systemPrompt": "{{Inkwell}}"`，主服务器变量替换时经 `agent_map.json` 解析到 `Agent/Inkwell.txt`。

因此新增一个 Agent = 写一个 `Agent/<Name>.txt` + `agent_map.json` 加一行 + `config.json` 的 `agents[]` 加一个对象。`AgentAssistant` 已在生产运行（已注册 21 个 Agent），无需启用、无需重启。

### 3.4 可复用的现成能力

| 能力 | 承载 | 用途 |
|---|---|---|
| 黑板 IO | `ServerFileOperator` | `ReadFile`（支持 `head:N` / `tail:N` / `M-N` 行范围）、`WriteFile`、`EditFile`、`AppendFile`、`ApplyDiff`、`FileInfo`、`ListDirectory` |
| AI 味量化 | `WeWriteHumanness` | 11 个统计/模式维度，<50ms 纯标准库。⚠️ **其综合分对小说不可用**，只能取 6 个适用维度自算，详见 §7.1 |
| 范文库 | `WeWriteExemplar` | 少样本风格引导 |
| 改稿飞轮 | `WeWriteLearnEdits` | 初稿 vs 终稿 diff 提炼 pattern，供长期文风进化 |
| 技能目录 | `SkillBridge` | `SKILL/<name>/SKILL.md`（YAML frontmatter + `references/*.md` 渐进披露），经 `{{VCPSkillBridge}}` 按需注入 |
| 进度可见 | `TopicSponsor` | `CreateTopic` / `ReplyToTopic` 向前端推送流水线时间线 |
| 长期记忆 | `DailyNote` + `RAGDiaryPlugin` | 沉淀编排经验与创作心得（不用于章节正文存储） |
| 多角色辩论 | `MagiAgent` | 后续可作 `Umpire` 的升级路径 |

### 3.5 关键陷阱

- **`ServerFileOperator` 的 `WriteFile` 同名不覆盖，会自动改名**成 `status_card(1).md`。刷台账必须用 `EditFile`（文件不存在则报错），首次创建才用 `WriteFile`。
- 交接链深度上限为 5（`handoffMaxDepth`）。采用中心辐射拓扑（所有 `callback_agent` 都回呼 `Muse`），深度恒为 1。
- `AgentAssistant` 的会话历史是纯内存、TTL 12 小时、保留 5 轮，重启即丢。跨 Agent 的产物传递一律靠落盘 + 路径。
- `委托 prompt` 值里严禁出现 `<<<[TOOL_REQUEST]>>>`、`「始」`、`「末」`。

---

## 4. Agent 编制

### 4.1 编制取舍

每章的串行工序数 × 章数 = 总时长，是本方案的成本命门。

| 编制 | 每章工序 | Agent 数 | 30 章委托次数 | 结论 |
|---|---|---|---|---|
| A 精编流水线 | 5 棒：责编→执笔→审稿→修订→记账 | 8 | ~150 | 质量上限最高，串行下过慢 |
| **B 折中（采纳）** | **3 棒常规** + 不过时插 1 棒修订 | **6** | **~100** | 保住"写手≠审稿"命门，责编并入执笔 |
| C 自主执笔 | 2 棒，审稿只在弧末 | 5 | ~70 | 最省，单章质量失控，网文最忌 |

采纳 B。关键判断：把"责编出节拍表"并进执笔（同一棒内先落盘 plan 再写正文，ainovel-cli 的 Writer 即如此），省一棒且不损质量；但审稿绝不能并进执笔或降为弧级——网文的爽感密度、章末钩子、人设漂移必须逐章卡。

### 4.2 花名册

| Agent | 代号 | chineseName | 职责 | 只读什么（隔离边界） | temperature |
|---|---|---|---|---|---|
| 总编 | `Muse` | 缪斯 | **只编排不替工**：调度、传路径、`FileInfo` 验证、人工检查点、失败分诊 | 台账摘要 + 下游回执 | 0.3 |
| 架构师 | `Atlas` | 架构师 | 世界观 / 角色 / 分层大纲 / 指南针；弧或卷结束时滚动展开下一段 | 指南针 + 弧摘要 + 角色档案 | 0.6 |
| 执笔 | `Inkwell` | 执笔 | 一棒内完成：出节拍表 → 写正文 → 自检 → 落盘 | 节拍表 + 状态卡 + 伏笔池 + 最近 3 章摘要 + 文风契约 | 0.85 |
| 审稿 | `Umpire` | 审稿 | 对抗式审稿，**默认拒稿**。先跑 `WeWriteHumanness`，再做六维 LLM 评审，每项引用原文举证 | 正文 + 节拍表 + 台账 + 雷点清单 | **0.01** ⚠️ |
| 修订 | `Chisel` | 修订 | 只按 verdict 的 top-3 待修项精修，不重写全文（仅审稿不过时启用） | 正文 + verdict top-3 + 禁用风格黑名单 | 0.5 |
| 记账 | `Ledger` | 记账 | 一棒刷完四本账 + 写章摘要 | **只读终稿正文** + 上一版台账 | 0.2 |

可选第七位 `Echo`（读者，弧级追读体验五视角评审，temperature 0.7）留作阶段三。

### 4.3 两条刻意的隔离边界

**`Ledger` 不读 `plan` 与 `verdict`。** novelforge 有专门的回归测试守这条，称为防"立场后门"：记账员若看过评审意见，会把"评审说这里写得好"当成事实写进状态卡，污染后续所有章节。`Ledger` 的输入只有终稿正文与上一版台账。

**`Chisel` 独立于 `Inkwell`。** 让写手改自己的稿，它会为自己辩护；且温度职能不同（0.85 用于发挥，0.5 用于精修）。成本只在审稿不过时才发生。

### 4.4 模型分配

分配原则不是"贵的更好"，而是**按调用频次与质量敏感度的乘积花钱**。这两个量在本方案里恰好是反相关的：调用最频繁的角色（总编、记账）质量敏感度最低，质量敏感度最高的角色（执笔、审稿）调用频次中等。所以存在一个明确的最优解，而不是均匀降级。

下表所有 modelId 都已实测调通，见 §4.4.1：

| Agent | modelId | 计费 | 每 30 章调用量 | 定档理由 |
|---|---|---|---|---|
| `Muse` 总编 | `gemini-3.6-flash` | 免费 | **~100**（最高） | 不需要创造力，只需照 6 份模板逐字执行。**同型先例**：`Muse.txt` 的原型 `Helm` 在生产中就跑免费小模型档，且 `Helm.txt` 提示词比 `Muse.txt` 更长——编排角色不吃模型智力 |
| `Atlas` 架构师 | `deepseek-v4-pro` | 付费 | ~3（最低） | **原定 `gemini-3.6-flash`，实测后升档**，原因见 §4.4.2。每本书仅跑约 3 次，升档成本可忽略 |
| `Inkwell` 执笔 | `deepseek-v4-pro` | 付费 | 30 | **产品本身**，唯一不省的地方。同上路由的 `literary_writing` 指向的就是 DeepSeek-V4-Pro |
| `Umpire` 审稿 | `deepseek-v4-pro` | 付费 | ~40 | 它是判官。弱模型审稿的典型失效不是错杀而是**一律放行**，质量门禁会退化成橡皮章，整套 PASS/FAIL 机制随之作废 |
| `Chisel` 修订 | `deepseek-v4-pro` | 付费 | ~10 | 单看智力要求最低（verdict 已给出确切改法），但它改的是**已由 pro 写成的正文**。降档会在同一章内留下文风断层，比整章统一略弱更糟 |
| `Ledger` 记账 | `deepseek-v4-pro` | 付费 | 30 | **原定 `gemini-3.6-flash`，实测后升档**，原因见 §11.9。免费档四次升级式失败，最严重的一次破坏了伏笔 ID 系统 |

**结果（已按实测修正）**：约 100 次调用免费（仅 `Muse`）、113 次付费。付费调用量比"六个全上 pro"降约 47%——**远低于最初估算的 61%**，因为两个原定免费的岗位（`Atlas`、`Ledger`）都因伪造行为被迫升档。

> **免费档的适用边界（实测收敛出的结论）：产出是"给人看的文字"可以用免费档；产出是"给下游程序读的结构化资产"不能。** `Muse` 之所以还留在免费档，是因为它的产出是委派动作与一份两行的 `run.md`，且它的每一步都有外部断路器与用户可见的回执兜着。

#### 4.4.1 可用性实测（2026-08-06）

模型 ID 取自上游 `/v1/models`，并逐个发过最小 `chat/completions` 验证，不是照名单猜的：

| modelId | 结果 | 首字延迟 |
|---|---|---|
| `deepseek-v4-pro` | OK | ~1.3s |
| `deepseek-v4-flash` | OK | ~0.7s |
| `gemini-3.6-flash` | OK | ~2.5s |
| `qwen3.6:latest` | **HTTP 500** | 21s 后失败 |

⚠️ **本地 `qwen3.6:latest` 当前不可用，且这不是本方案独有的问题。** 网关（new-api）返回 `upstream error: do request failed`；查本地 Ollama（`127.0.0.1:11434`）实际只装了 `llama3.2:3b`、`bge-m3:latest`、`llama3:latest`——**`qwen3.6` 并未 pull 到本地**，通道指向了一个不存在的模型。

连带影响：现有 `config.json` 里 `HELM`、`HELM_CN`、`QUILL` 三个 Agent 都配在 `qwen3.6:latest` 上，**它们现在应该也是调不通的**。要么 `ollama pull` 对应模型，要么把这三个也改到 `gemini-3.6-flash`。本方案因此不依赖本地模型，总编改用已验证可用的 `gemini-3.6-flash`。

代价是免费位都压在同一个云端后端上，存在共同限流风险。若试跑中出现 429，把 `Ledger` 挪到 `deepseek-v4-flash`（实测最快，0.7s，付费但单价低于 pro）即可分流——它是免费位中输入最长、最吃吞吐的一个。

#### 4.4.2 免费 flash 档会伪造工具验证结果（实测，2026-08-06）

冒烟测试第一棒（`Atlas` 建基，当时跑 `gemini-3.6-flash`）暴露了一个比"写错参数"严重得多的失效形态：**它把文件写对了，但把验证环节整段编造出来。**

实测对比（`state/` 台账，回执声称 vs 磁盘实际）：

| 文件 | 回执声称 | 磁盘实际 |
|---|---|---|
| `status_card.md` | 366 B | 457 B |
| `hooks.md` | 247 B | 151 B |
| `hooks_closed.md` | 181 B | 126 B |
| `timeline.md` | 139 B | **文件不存在** |
| `cognition.md` | 122 B | 121 B |
| `progress.md` | 162 B | 150 B |

12 份产物实际写对 11 份（目录骨架、6 份设定文件、5 份台账表头都完全合规，无 `xxx(1).md` 撞名），但 `timeline.md` 被静默漏写，而 Step A4 要求的 `FileInfo` 逐份验证与 `ListDirectory` 计数**根本没有真正执行**——所有字节数都是编的，包括一个不存在文件的字节数。任务最终以 `Succeed` 收尾。

**为什么这条比参数写错更危险：** 参数写错会报错，会被"同一工具连续失败 2 次即停手"拦住，是响亮的失败。伪造验证是无声的成功，它同时骗过了自己的自检和总编的回执核对。

**这动摇了本方案的一处防御设计。** §9 失败模式表里防"下游报假路径"的手段是"`Muse` 独立 `FileInfo` 二次验证"，而失效的恰恰就是这条纪律本身。`Muse` 与 `Ledger` 同样跑在 `gemini-3.6-flash` 上，需在试跑中重点观察它们是否同样伪造。

**已采取的处置：** `Atlas` 升到 `deepseek-v4-pro`。它每本书只跑约 3 次，升档几乎不增加成本，而建基产物是下游所有环节的地基。

#### 4.4.3 对照结论：伪造只发生在"工具返回的数字"上（实测，2026-08-06）

同日跑完四棒后，`gemini-3.6-flash` 与 `deepseek-v4-pro` 形成了干净的两组对照：

| 岗位 | 模型 | 回执里的 `FileInfo` 字节数 |
|---|---|---|
| `Atlas` 架构师 | `gemini-3.6-flash` | 6 项全错，含 1 个不存在文件 |
| `Ledger` 记账 | `gemini-3.6-flash` | 6 项全错（一律偏小，如 1221 vs 实际 1429） |
| `Inkwell` 执笔 | `deepseek-v4-pro` | 2 项全对（2.29 KB / 8.71 KB 与 2348 B / 8917 B 吻合） |
| `Umpire` 审稿 | `deepseek-v4-pro` | 三项客观评分与独立实测**完全一致**（15.82 / 32.86 / 0.5099） |

**但 flash 的实质工作是对的。** `Ledger` 这一棒的命令选择全部正确：`timeline.md` 用 `AppendFile`（表头与旧行完整保留）、三份快照台账用 `EditFile`、`summaries/ch001.md` 用 `WriteFile` 且无撞名改名、本章无已闭合伏笔时正确地没有动 `hooks_closed.md`。台账内容质量也高：伏笔 ID 规范、预计回收章无一留空、债务算对、`cognition.md` 严格只有两个字段。

**所以伪造是有边界的：** flash 会编造"只有工具返回值才能知道的数字"，而不会编造需要理解任务才能做对的事。它不是整体不可信，而是**在需要引用工具输出时倾向于顺手编一个看起来合理的值**。

**这改变了处置思路。** 原本的方向是"把伪造的岗位升档"，但 `Ledger` 每章 1 次、`Muse` 每章 3–4 次，全升档会让 §4.4 的省钱结论基本作废。更准确的结论是：

- **自报字节数本身没有信息价值**，因为它既可能是真的也可能是编的，而验证者（`Muse`）同样跑在 flash 上。**不应把它当作产物落盘的证据**，回执模板里要求它反而制造虚假的安全感。
- **真正危险的不是数字错，而是数字掩盖了漏写。** `Atlas` 的案例里，伪造的验证盖住了一份真实缺失的 `timeline.md`。
- **漏写在下游会自然暴露**：缺文件会让下一棒 `ReadFile` 报 `ENOENT` 并如实上报。`Atlas` 那一棒之所以危险，恰恰因为它是 bootstrap，上游没有任何环节能兜住它。

**处置：** 结构性单点（`Atlas` 建基，全书仅 3 次）升 `deepseek-v4-pro`；高频岗位（`Ledger`、`Muse`）留在免费档，靠下游 `ENOENT` 自然暴露漏写。不要仅因回执数字对不上就升档。

⚠️ `Umpire` 的 `temperature` 填 **`0.01`** 而不是 `0`。`AgentAssistant.js:201` 是 `parseFloat(temperature || '0.7')`，而 JS 里 `0 || '0.7'` 等于 `'0.7'`——填 0 会被静默改成 0.7，审稿的确定性（本方案的质量命门）会失效且无任何报错。

**降档/升档的观察点**（前 5 章试跑时重点盯，每一项都只是一行 config 改动）：

- `Ledger` 若把 `AppendFile` 与 `EditFile` 用混、或写错 `filePath`/`directoryPath` 参数名 → 升到 `deepseek-v4-flash`。这类参数错误是 flash 档最可能的失效形态，而记账写错会顺台账污染全书。
- `Muse` 若出现模板走形（`「始」「末」` 分隔符丢失、把 TOOL_REQUEST 包进代码块、伪造 `delegationId`）→ 升到 `deepseek-v4-flash`。这是编排层唯一不可容忍的失效类型：写坏一章可以重跑，编排走形会让整条链断在中途。
- `Inkwell` 是唯一值得主动 A/B 的位置：它既是最大成本项，也是模型"嗓音"差异最明显的地方。建议第 1 章用 `deepseek-v4-pro` 与 `gemini-3.6-flash` 各跑一遍，交给 `Umpire` 盲评六维分再定。若 flash 的分差在 1 分以内，整本书的主要成本就消失了。

#### 4.4.4 免费档会在当日额度耗尽后整体不可用（实测，2026-08-06）

一天的冒烟测试（约两章，含一次被复制的下游链）之后，`gemini-3.6-flash` 开始稳定返回上游 429：

| modelId | 探测结果 |
|---|---|
| `gemini-3.6-flash` | **502 / 上游 `HTTP Error 429: Too Many Requests`，间隔 2 分钟后重探仍 3/3 全败** |
| `deepseek-v4-pro` | 正常 |
| `deepseek-v4-flash` | 正常 |

间隔两分钟仍全败，说明不是每分钟速率窗口，而是**当日额度耗尽**。

**这动摇了 §4.4 的成本结论的前提。** 那份省钱测算假设免费档随时可用，于是把调用量最大的两个岗位（`Muse` 每章 3–4 次、`Ledger` 每章 1 次）都放在上面。实际上免费档是**有日上限的共享资源**，而这两个岗位恰好是最快把它烧穿的。烧穿之后整条链直接停摆——不是降质，是不可用。

**注意并发缺陷在这里有成本后果。** §11.5 ② 那次重复委派把下游整链复制了一份，多出的审稿、打磨、记账调用全部记在免费额度上。数据污染可以清理，烧掉的额度当天回不来。

**已选处置：第 1 条（等次日额度恢复，不改模型分配）。** 保留 §4.4 的成本方案，接受"撞上 429 就停工"。选它的前提是当前仍在验证阶段、没有交付压力；一旦进入正式连续写作，第 2 条会更合适。

三条路各有代价：

1. **免费档 + 撞上 429 就停工等次日**：成本最低，但把"能不能写"绑在额度上，且 429 发生在链路中途时会留下半成品状态（`在途工序` 卡住，需人工判断续跑点）。
2. **高频岗位换 `deepseek-v4-flash`**：实测可用，比 `pro` 便宜。付费调用量上升但换来可预期的可用性。这是目前看最务实的一条。
3. **免费档为主 + 429 时自动降级到 `deepseek-v4-flash`**：最理想，但 `AgentAssistant` 的 `modelId` 是 config 里的静态字段，**没有失败降级机制**，实现它需要改插件代码，违背"零新增代码"的既定约束。

无论选哪条，`run.md` 的 `在途工序` 字段都必须能表达"因上游不可用而中断"，否则续跑时分不清是在途还是已死。

#### 4.4.5 免费档日上限的量化值：约 30 分钟工作量（实测，2026-08-07）

次日额度恢复后（探针 3/3 通过）跑第 3 章，**到 10:22 又耗尽**，当天有效工作窗口只有 09:53–10:22 约 30 分钟。消耗构成：

| 来源 | 次数 | 说明 |
|---|---|---|
| `Muse` 委派记录 | 6 | 含回调唤醒 |
| `Ledger` 委派记录 | 1 | 每章固定 1 次 |
| 直连 `/v1/chat/completions` 的缪斯对话 | 约 5 | 启动 / 补发 / 续跑 / 复跑 / 第 4 章尝试 |
| **合计会话** | **约 12** | 每次多轮展开，实际 API 调用约 30–40 次 |

**结论：免费档的日上限约在数十次调用量级，撑不住一章的完整编排。** 一章正常需要缪斯 3–4 次唤醒（每次可能十余轮）加记账 1 次；只要中途出一次差错（如 §11.7 那次 11 连发），当天即停摆。

**第 4 章的 ⑤⑥ 复验因此被阻断**——502 发生在缪斯启动前，未留半成品状态，黑板保持在 `待用户验收: ch003` 的干净点。

**决定：仍选第 1 条（等次日，不改模型分配）。** 理由与 §4.4.4 相同——仍在验证阶段、无交付压力。但需明确记下代价：**按此配置，验证节奏的上限是"每天一章、且当天不容出错"**。进入正式连续写作前必须改选第 2 条。

> ⚠️ **另有一条与成本无关但更值得注意的观察：截至 2026-08-07，所有已修的纪律性缺陷都出在免费档岗位上。**
>
> | 岗位 | 档位 | 缺陷 |
> |---|---|---|
> | `Muse` | flash（免费） | §11.5 ② 重复委派、§11.7 漏读状态 / 缺终止符 / 分支误选 |
> | `Ledger` | flash（免费） | §11.5 ④ 漏换行、§11.7 重复写摘要、`hooks_closed` 漏换行 |
> | `Atlas` | 原 flash，已升 pro | §4.4.2 伪造 `FileInfo` |
> | `Inkwell` | pro | 无 |
> | `Umpire` | pro | 仅 §11.5 ①，由注入文档误导所致，与档位无关 |
>
> `pro` 档岗位至今零纪律性故障。这提示第 2 条（高频岗位换 `deepseek-v4-flash`）的收益可能不只是可用性，**还包括减少这类需要反复打补丁的失败**——但 `deepseek-v4-flash` 是否也属"会重做已完成步骤"的档位尚未实测，换档前不应假定它等同 `pro`。

## 5. 黑板：文件即记忆

### 5.1 目录结构

根目录 `file/novel/<book-id>/`，与既有 `file/document/DeepSearch/` 并列。

```
file/novel/<book-id>/
├── book.md                  # 元信息：书名 / 题材 / 主角 / 目标章数 / 目标篇幅
├── compass.md               # 指南针：终局方向 + 活跃长线 + 规模估计
├── outline.md               # 分层大纲：当前卷详细章节 + 后续卷仅 goal + 预估章数
├── characters.md            # 角色档案
├── world.md                 # 世界规则与设定
├── style.md                 # 本书文风契约 + 禁忌清单 + 题材禁用风格黑名单
├── state/
│   ├── status_card.md       # 当前状态卡（整份覆盖）
│   ├── hooks.md             # 伏笔池
│   ├── timeline.md          # 时间线
│   ├── cognition.md         # 角色认知
│   ├── progress.md          # 内容事实：已完成章 / 当前弧（Ledger 写）
│   └── run.md               # 编排运行态：在途工序 / 修订轮次（Muse 写）
├── plans/chNNN.plan.md      # 章节节拍表
├── chapters/chNNN.md        # 章节终稿
├── reviews/chNNN.verdict.md # 评审判决
├── summaries/chNNN.md       # 章摘要
├── arcs/arcNN.md            # 弧摘要
└── log/debt.md              # 带病上线记录
```

`chNNN` 三位零填充（`ch001`）。`book-id` 用 kebab-case ASCII，避免中文路径在工具链中的编码问题。

### 5.2 四本账的字段规范

**`state/status_card.md` —— 当前状态卡**（由 `Ledger` 整份覆盖）

```markdown
# 状态卡 · 截至第 NNN 章

## 进度锚点
- 最新完成章: 第 NNN 章《标题》
- 当前卷 / 弧: 第 N 卷 · 第 N 弧（弧型: 成长突破）
- 故事内时间: <故事内的当前时间点>
- 主角当前位置: <地点>

## 主角状态
- 实力 / 境界: <当前> （上章为 <前值>）
- 身份 / 处境: <一句话>
- 持有关键物: <清单>
- 当前目标: <短期目标>
- 当前主要障碍: <一句话>

## 关键配角在场状态
| 角色 | 当前处境 | 与主角关系 | 最近出场章 |
|---|---|---|---|

## 势力 / 组织态势
- <势力名>: <当前态势>
```

**`state/hooks.md` —— 伏笔池**

```markdown
# 伏笔池 · 截至第 NNN 章

| ID | 伏笔内容 | 埋设章 | 最近推进章 | 预计回收章 | 状态 | 债务 |
|---|---|---|---|---|---|---|
| H001 | <一句话> | 003 | 012 | 030 | 推进中 | 18 |
```

- `状态` ∈ {已埋设, 推进中, 已回收, 已废弃}
- `债务` = 当前章号 − 最近推进章号。债务 > 20 时 `Umpire` 应在评审中标注；> 40 时 `Muse` 应提醒用户。
- 已回收的伏笔保留在表中，状态改为"已回收"，便于审稿核对回收是否成立。

**`state/timeline.md` —— 时间线**

```markdown
# 时间线

| 故事内时间 | 事件 | 涉及角色 | 出处章 |
|---|---|---|---|
```

**`state/cognition.md` —— 角色认知**

这本账专治一类高频崩坏：角色知道了不该知道的信息。网文里信息差是剧情引擎，值得单列。

```markdown
# 角色认知 · 截至第 NNN 章

## <角色名>
- knows: <该角色已知的关键信息清单>
- does_not_know: <该角色尚不知情的关键信息清单>
- reader_knows_but_character_does_not: <读者已知但该角色不知的信息差>
```

**`state/progress.md` —— 内容事实**（唯一写入者：`Ledger`）

```markdown
# 进度

- 已完成章数: N
- 最新完成章: NNN
- 累计字数: NNNNN
- 当前弧: arcNN（起始章 NNN，预计结束章 NNN）
```

**`state/run.md` —— 编排运行态**（唯一写入者：`Muse`）

```markdown
# 运行态

- 在途工序: <无 | 执笔 ch012 | 审稿 ch012 | 修订 ch012 | 记账 ch012>
- 本章修订轮次: 0
- 待用户验收: <无 | ch012>
- 最后更新: YYYY-MM-DD HH:mm
```

### 5.6 单写入者原则

台账刷新用 `EditFile`（整份覆盖），因此**每个文件必须只有一个写入者**，否则两个 Agent 会互相清掉对方的字段。这是把运行态从 `progress.md` 拆出来单独成 `run.md` 的原因：`在途工序` 必须在每道工序**开始前**就写入才有崩溃恢复价值，只有 `Muse` 处在那个时机；而 `Ledger` 是每章最后一棒，只能记录已完成的内容事实。

| 文件 | 唯一写入者 |
|---|---|
| `book.md`、`compass.md`、`outline.md`、`characters.md`、`world.md`、`style.md` | `Atlas` |
| `plans/chNNN.plan.md`、`chapters/chNNN.md` | `Inkwell`（`chapters/chNNN.md` 在修订时由 `Chisel` 覆盖） |
| `reviews/chNNN.verdict.md` | `Umpire` |
| `state/status_card.md`、`state/hooks.md`、`state/timeline.md`、`state/cognition.md`、`state/progress.md`、`summaries/chNNN.md` | `Ledger` |
| `arcs/arcNN.md` | `Atlas` |
| `state/run.md`、`log/debt.md` | `Muse` |

`Muse` 因此需要 `inject_tools` 里带 `ServerFileOperator`（它本身也需要用来做 `FileInfo` 二次验证）。

### 5.3 章节节拍表 `plans/chNNN.plan.md`

由 `Inkwell` 在写正文前落盘，是 `Umpire` 核对"履约"的依据。

```markdown
# 第 NNN 章 节拍表

- 章节类型: <推进 | 爆发 | 铺垫 | 转折 | 日常过渡>
- 目标字数: 3000
- 本章目标: <一句话，本章要完成什么>
- 核心冲突: <一句话>
- 情绪弧线: <起点情绪 → 终点情绪>

## 场景推进项
1. <场景一要发生的事>
2. <场景二要发生的事>

## 爽点设计
- 位置: <约第 N 段 / 全章 X% 处>
- 类型: <打脸 | 反转 | 实力揭示 | 情感回报>

## 章末钩子
- 类型: <悬念 | 危机 | 信息揭示 | 情感悬置>
- 具体内容: <一句话>

## 必须推进的伏笔
- H00X: <如何推进>

## 必须保留的一致性约束
- <从状态卡 / 角色认知中提取的硬约束>
```

### 5.4 评审判决 `reviews/chNNN.verdict.md`

```markdown
# 第 NNN 章 评审判决

- 判定: <PASS | FAIL>
- 文体校正 AI 味分: NN / 100（仅计 6 个对小说适用的维度，越低越好；算法见 §7.1）
- 插件原始综合分: NN.N（含 5 个对小说不适用维度，仅记录，**不作判据**）
- 统计层薄弱项: <6 个适用维度中得分 < 0.6 的项及其 detail>

## 六维评分（每项 0-10，必须引用原文举证）
| 维度 | 得分 | 证据（原文引用） | 问题 |
|---|---|---|---|
| 爽感密度 | | | |
| 设定自洽 | | | |
| 节奏张力 | | | |
| 人设一致 | | | |
| 叙事衔接 | | | |
| 追读引力 | | | |

## 节拍表履约核对
| 节拍表条目 | 是否落实 | 说明 |
|---|---|---|

## 待修项（按严重度排序，最多 3 条交给 Chisel）
1. [阻塞|高|中|低] <问题> —— 原文位置: <引用> —— 建议: <具体改法>
```

判定规则：任一维度 ≤ 4 分，或存在"阻塞"级待修项 → `FAIL`。

### 5.5 带病上线 `log/debt.md`

防死循环机制。`Chisel` 修订两轮后 `Umpire` 仍判 `FAIL` 时，记一笔债务并放行，绝不无限重试。

```markdown
| 章号 | 遗留问题 | 严重度 | 记录时间 |
|---|---|---|---|
```

---

## 6. 工作流

### 6.1 编排拓扑：中心辐射

所有下游的 `callback_agent` 都指向 `Muse`，交接深度恒为 1，不会撞上 `handoffMaxDepth` 的 5 层上限。

```
用户给设定
  └─ Muse 开进度话题（TopicSponsor CreateTopic）
       └─ 委派 Atlas 建世界观 / 角色 / 分层大纲 / 指南针
            └──▶ ★检查点1：用户确认设定
                 └─ 每章循环（全队同时只有 1 个在途委托）：
                      ① 委派 Inkwell ── 出节拍表 + 写正文 + 自检 + 落盘
                      ② 委派 Umpire ── WeWriteHumanness + 六维评审
                           ├─ PASS ──────────────────────┐
                           └─ FAIL → ③ 委派 Chisel 精修 ──┤
                                       （两轮仍 FAIL → 记 debt 放行）
                      ④ 委派 Ledger ── 刷四本账 + 章摘要
                      └──▶ ★检查点2：用户验收本章
                 └─ 每 5 章（弧末）：
                      委派 Atlas 写弧摘要 + 展开下一弧
                      └──▶ ★检查点3：用户确认下一弧方向
```

### 6.2 `Muse` 的事件表

沿用 `Helm` 的 `HELM_EVENT:*` 命名惯例，前缀改为 `MUSE_EVENT:`。收到任何回呼时，**第一件事是读回执的 `状态:` 字段**，而非事件名。

| 事件 | 状态 succeed 时的动作 | 是否等用户 |
|---|---|---|
| `MUSE_EVENT:ATLAS_FOUNDATION_COMPLETE` | `FileInfo` 验证 6 份设定文件 → `ReplyToTopic` → 汇报设定摘要 | ✅ 等确认 |
| `MUSE_EVENT:INKWELL_DRAFT_COMPLETE` | `FileInfo` 验证 `chNNN.md` 与 `chNNN.plan.md` → 立即委派 `Umpire` | ❌ 自动推进 |
| `MUSE_EVENT:UMPIRE_VERDICT_COMPLETE` | 读 verdict 判定：PASS → 委派 `Ledger`；FAIL 且修订轮次 < 2 → 委派 `Chisel`；FAIL 且轮次 = 2 → 追加 `log/debt.md` 后委派 `Ledger` | ❌ 自动推进 |
| `MUSE_EVENT:CHISEL_REVISE_COMPLETE` | `FileInfo` 验证 → 修订轮次 +1 → 重新委派 `Umpire` | ❌ 自动推进 |
| `MUSE_EVENT:LEDGER_BOOKKEEP_COMPLETE` | `FileInfo` 验证四本账 + 章摘要 → `ReplyToTopic` → 汇报本章完成 | ✅ 等验收 |
| `MUSE_EVENT:ATLAS_ARC_EXPAND_COMPLETE` | `FileInfo` 验证弧摘要与新大纲 → 汇报下一弧方向 | ✅ 等确认 |
| 任意事件 + 状态 failed | `ReplyToTopic` 推送失败原因 → 向用户如实汇报 → 停下等用户决定重试 / 跳过 / 终止 | ✅ 等决定 |

### 6.3 委派模板

以委派 `Inkwell` 为例。所有委派遵循同一形态：`task_delegation:true` + `callback_on:always` + `callback_agent:Muse` + `inject_tools` 注入读取工具 + prompt 里只传路径不传长文。

```
<<<[TOOL_REQUEST]>>>
maid:「始」缪斯「末」,
tool_name:「始」AgentAssistant「末」,
agent_name:「始」执笔「末」,
task_delegation:「始」true「末」,
callback_on:「始」always「末」,
inject_tools:「始」ServerFileOperator「末」,
callback_agent:「始」Muse「末」,
callback_prompt:「始」MUSE_EVENT:INKWELL_DRAFT_COMPLETE
委托ID: {{delegation_id}}
来源Agent: {{source_agent}}
状态: {{status}}
任务归档(AgentAssistant自动存档,非正文): {{archive_abspath}}
章号: NNN
topic_id: <topic_id, 无则写"无">

完成报告:
{{report}}「末」,
callback_inject_tools:「始」AgentAssistant,ServerFileOperator,TopicSponsor「末」,
callback_maid:「始」AgentAssistant自动交接「末」,
prompt:「始」请撰写第 NNN 章。书目录: E:\VCPChat\VCPToolBox\file\novel\<book-id>\

必读文件（用 ServerFileOperator ReadFile 逐个读取）:
- outline.md（取第 NNN 章的大纲条目）
- state\status_card.md
- state\hooks.md
- state\cognition.md
- style.md
- summaries\ch<NNN-3>.md ~ ch<NNN-1>.md（最近三章摘要）

产出（两份，均落盘后立刻 FileInfo 验证）:
1. plans\chNNN.plan.md —— 先出节拍表，格式见 novel-ledger 技能
2. chapters\chNNN.md —— 再据节拍表写正文，目标 3000 字

[[TaskComplete]] 回执必须包含: 两份文件的验证通过绝对路径、本章一句话概要、本章推进了哪些伏笔。「末」
<<<[END_TOOL_REQUEST]>>>
```

委派后把返回的 `{{VCP_ASYNC_RESULT::AgentAssistant::<delegationId>}}` 占位符原样写进回复，本轮立即结束，不轮询。

### 6.4 崩溃恢复

`Muse` 在每次发起委派**之前**先用 `EditFile` 把 `state/run.md` 的 `在途工序` 更新为即将开始的工序。这样它被唤醒且无在途回执时，读 `state/run.md` 的 `在途工序` 与 `本章修订轮次` 即可推导下一步：

| `在途工序` | 恢复动作 |
|---|---|
| 无 | 委派 `Inkwell` 写下一章 |
| 执笔 chNNN | `FileInfo` 检查 `chNNN.md` 是否已落盘；有则委派 `Umpire`，无则重派 `Inkwell` |
| 审稿 chNNN | 检查 `chNNN.verdict.md`；有则按判定推进，无则重派 `Umpire` |
| 修订 chNNN | 重派 `Umpire` 复审 |
| 记账 chNNN | 检查四本账更新时间；未更新则重派 `Ledger` |

---

## 7. 新增 Skill

放在 `Plugin/SkillBridge/SKILL/<name>/SKILL.md`，沿用现有格式（YAML frontmatter 含 `name` / `description` / `license` / `metadata`，正文用 Quick Reference 表 + `references/*.md` 渐进披露）。`description` 里要写 Triggers 关键词。

| Skill | 内容 | 使用者 |
|---|---|---|
| `webnovel-craft` | 爽感密度与投放节奏、章末钩子类型库、张弛节奏、信息投放速度、视角控制、对话与心理描写规则 | `Inkwell`、`Umpire` |
| `story-architecture` | 五种弧型模板（成长突破 / 竞技对抗 / 探索发现 / 恩怨冲突 / 日常过渡，各含参考密度与适用题材）、角色弧光、伏笔埋设与回收、指南针写法；`references/genre-*.md` 存题材 preset | `Atlas`、`Umpire` |
| `manuscript-review` | 六维评分锚点、举证硬要求、严重度分级、骨架检测器（防模型照抄示例里的占位符）、**`WeWriteHumanness` 的虚构文体适配用法（含 11 维适用性实测表）** | `Umpire`、`Chisel` |
| `novel-ledger` | 四本账字段规范与刷新规则、伏笔债务算法、节拍表与 verdict 的模板、`EditFile` vs `WriteFile` 的使用纪律 | `Ledger`、`Inkwell`、`Umpire` |

去 AI 味的规则分两处承载：**客观可测的部分**交给 `WeWriteHumanness` 的适用维度（AI 套话黑名单、副词密度、句长方差、段落节奏），**文体特有的部分**写进 `webnovel-craft` 的反面清单与题材 preset 的禁用风格黑名单。原计划是"全部交给插件、Skill 只讲怎么调用"，实测后证明不可行，原因见 §7.1。

### 7.1 `WeWriteHumanness` 用于虚构文体的适配（实测修正）

**这是一处必须记录的设计修正。** 原方案假定该插件的 0–100 综合分可直接用作小说的客观质量信号，实测证明该假设错误。

用一段刻意写得克制、有人味的仙侠开篇（668 字）实测，插件给出综合分 **52.3 / 100**，判定「❌ AI 味较重，建议改稿」，`rewrite_recommended: true`。逐维度拆开后发现，11 个维度里有 5 个**对虚构文体结构性不适用且恒定得 0 分**：

| 不适用维度 | 恒 0 的原因 |
|---|---|
| `real_sources` 真实信源 | 要求具名人名/百分比/年份/金额。小说没有引注，**无法满足** |
| `word_temperature_mix` 词汇温度混搭 | 要求混用「边际/认知负荷」「说白了」「DNA动了/卷」「整挺好」四档现代语域。**这些正是仙侠题材的禁用词**，与文风契约直接冲突 |
| `self_correction` 自我纠正 | 要求「不对，准确说……」这类议论文作者自我修正。第三人称限知叙述者不会这样说话 |
| `negative_emotion_ratio` 负面情绪占比 | 靠评价性词汇（吐槽/质疑/不满）计数。小说用动作与生理反应呈现情绪，**写得越好这项分越低** |
| `broken_sentences` 破句结构 | 实测找到 6 处、原始分 1.00，被钟形曲线校准（center=0.39）**倒扣到 0.00**——因为"太多了"。该校准按议论文密度设定，对小说是反向惩罚 |

而 6 个适用维度表现良好：`adverb_density` 1.00、`paragraph_length_variance` 0.96、`banned_words` 0.93、`sentence_length_range` 0.85、`vocabulary_richness` 0.70、`sentence_length_stddev` 0.40，均值 0.81。

**结论与处置**：

1. `Umpire` 用 `mode: json`（**不用 `summary`** —— summary 只回传最弱 5 维，而小说的最弱 5 维恒定是上述 5 个不适用维度，等于拿不到有效信息）。
2. 只取 6 个适用维度自算：`文体校正 AI 味分 = (1 − 六项均值) × 100`。同一样本按此算得约 **19 分**，属「人类感很强」——与插件原始判定相反，差 33 分。
3. 不传 `tier3_score`，它只参与那个对小说无意义的综合分。
4. 原始综合分记入 verdict 但显式标注"不作判据"，防止下游误用。
5. 这 6 项都**不单独构成 FAIL**，只作为六维语义评审的扣分线索。

阶段三若做 `NovelLint` 插件，应把这 6 个适用维度 + 小说特有的确定性检查（钩子类型去重、伏笔债务、境界跳跃）合并为一个面向虚构文体的原生打分器，届时不再依赖 `WeWriteHumanness`。

题材 preset 按 novelforge 的思路：新建一本书时从 `references/genre-*.md` 拷一份内容进该书的 `style.md`，拷完与 preset 解耦，之后改动互不影响。

---

## 8. 落地清单

### 8.1 新增文件

```
Agent/Muse.txt
Agent/Atlas.txt
Agent/Inkwell.txt
Agent/Umpire.txt
Agent/Chisel.txt
Agent/Ledger.txt
Plugin/SkillBridge/SKILL/webnovel-craft/SKILL.md
Plugin/SkillBridge/SKILL/story-architecture/SKILL.md
Plugin/SkillBridge/SKILL/story-architecture/references/genre-xianxia.md
Plugin/SkillBridge/SKILL/manuscript-review/SKILL.md
Plugin/SkillBridge/SKILL/novel-ledger/SKILL.md
```

Agent 定义文件的写法参照 `Agent/Helm.txt`（编排型）与 `Agent/MemoMaster.txt`（工种型）：顶部记忆/RAG 注入区，然后是角色定义、工作模式、执行流程、安全机制、输出规范。工具箱通过 `{{VarToolList}}`、`{{VCPFileToolBox}}`、`{{VCPCommunicationToolBox}}`、`{{VCPSkillBridge}}` 占位符引入。

### 8.2 修改文件

**`agent_map.json`** —— 追加 6 行：

```json
"Muse": "Muse.txt",
"Atlas": "Atlas.txt",
"Inkwell": "Inkwell.txt",
"Umpire": "Umpire.txt",
"Chisel": "Chisel.txt",
"Ledger": "Ledger.txt"
```

**`Plugin/AgentAssistant/config.json`** —— `agents[]` 追加 6 个对象。**不动 `delegationMaxRounds`，保持 15。**

原计划要提到 30，理由是"`Inkwell` 一棒内工具调用轮次会超过 15"。这个理由是错的：1 个 delegation 轮次 = 1 次完整的 `chat/completions` 调用，而单次调用**内部**的工具执行由主服务器的 VCP 循环负责，上限是 `config.env` 的 `MaxVCPLoopNonStream=5`。15 × 5 = 75 次工具机会，绰绰有余。而且该项在 `config.json` 顶层是**全局**设置，翻倍会把现有 21 个 Agent 的失控成本上限一起翻倍。真正需要观察的是 `delegationTimeout=1200000`（20 分钟），用第 1 章实测后按需只调它。

示例对象：

```json
{
  "baseName": "INKWELL",
  "chineseName": "执笔",
  "modelId": "deepseek-v4-pro",
  "description": "小说执笔 Agent，出节拍表并撰写章节正文",
  "systemPrompt": "{{Inkwell}}",
  "maxOutputTokens": 60000,
  "temperature": 0.85
}
```

`chineseName` 即 `agent_name` 调用名。

**只注册这 6 个，不要注册英文别名对象。** 原设计打算照现有 `Helm` / `主编` 的双注册做法再加 6 个英文别名，好让 `callback_agent` 用英文名。这个做法是错的：

```953:954:Plugin/AgentAssistant/AgentAssistant.js
    const userSessionId = `agent_${agentConfig.baseName}_delegation_session`;
    const lockKey = `${agentConfig.baseName}::${userSessionId}`;
```

委派的占线锁与会话历史**都由 `baseName` 派生**。注册 `MUSE` 和 `MUSE_EN` 两个 baseName，等于同一个 Agent 有两把独立的锁和两份互不相干的对话历史，"同一 Agent 不能同时派两个活"这条保护就失效了。

正确做法是让 `callback_agent` 直接用中文名（`callback_agent:「始」缪斯「末」`）。`AGENTS` 表以 `chineseName` 为键（`AgentAssistant.js:196`），回呼目标查的是同一张表（`delegationCallbacks.js:166-172`，**查不到直接 `throw`，不会降级为无回调**），所以中文名本身就能解析。本设计是中心辐射拓扑，只有 `Muse` 需要做回呼目标，英文别名一个都不需要。

### 8.3 生效方式

- 不新增插件，不改 `Plugin.js`、`server.js` 或任何 `routes/`。
- `agent_map.json` 与 `Agent/*.txt` 由 `modules/agentManager.js` 热监听，改动即时生效（有 prompt 缓存失效机制）。
- `Plugin/AgentAssistant/config.json` **没有文件监听器**。热重载只能通过 AdminPanel 触发：`POST /admin_api/agent-assistant/config` 保存配置时，`routes/admin/agentAssistant.js` 会调用插件的 `reloadConfig()`，立即更新内存中的 `AGENTS` 映射表，无须重启（见 `docs/AGENT_AND_TASK_SYSTEM_GUIDE.md` §2.1）。

  因此有两条路：**（推荐）在 AdminPanel 的 AgentAssistant 配置界面添加这 6 个 Agent**，保存即生效；或手工编辑 `config.json` 后重启服务器。手改文件而不重启不会生效。

- **新增 Skill 必须重启服务器。** `Plugin/SkillBridge/plugin-manifest.json` 的 `pluginType` 是 `static` 且**没有 `refreshIntervalCron`**，`Plugin.js` 只在 `initializeStaticPlugins` 时执行一次并把输出缓存在内存里。新建 SKILL 目录后手动跑 `node Plugin/SkillBridge/SkillBridge.js` 只更新磁盘上的 `skill-index.txt`，运行中的服务器仍提供启动时的旧值。**只验证索引文件会得出"已生效"的错误结论**，必须重启后才能在 `{{VCPSkillBridge}}` 里看到新技能。

  顺带修正 §3.4 的表述：`{{VCPSkillBridge}}` 注入的是**折叠索引**（`SkillBridge.js:191-201`），每条只有 Skill 名与绝对路径，Agent 必须自己 `ReadFile` 才拿到内容——这正是渐进披露的机制，不是正文自动注入。

### 8.4 相关既有文档

- `docs/AGENT_AND_TASK_SYSTEM_GUIDE.md` —— AgentAssistant 与 TaskAssistant 的权威配置文档，含 `config.json` 完整 schema 与管理 API。
- `Plugin/AgentAssistant/HANDOFF_DESIGN.md` —— 链式交接（`callback_*`）的设计文档。
- `docs/PLUGIN_ECOSYSTEM.md`、`docs/VCP同步异步插件开发手册.md` —— 阶段二若新增 `NovelLint` 插件时参考。

---

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 串行导致总时长偏长（30 章约 100 次委托） | 中篇需数小时至数天 | 接受。review 档本身就是分次推进；崩溃恢复保证可中断续跑 |
| `Inkwell` 单棒内超时不足 | 章节写不完 | **不动 `delegationMaxRounds`**（15 轮 × `MaxVCPLoopNonStream=5` = 75 次工具机会，够用；且它是全局设置，改动波及现有 21 个 Agent）。真正会先撞上的是 `delegationTimeout=1200000`（20 分钟），用第 1 章实测后按需只调它 |
| `Umpire` 过度拒稿导致每章都跑满修订轮次 | 成本翻倍 | 两轮上限 + 带病上线机制封顶；用前 3 章校准判定阈值 |
| 台账被 `WriteFile` 写成 `status_card(1).md` | 一致性彻底失效 | `novel-ledger` 技能与 `Ledger` 定义中双重强调用 `EditFile`；`Muse` 每章 `ListDirectory` 抽查 `state/` 目录有无带序号文件 |
| **累积型台账被整份覆盖时静默截断** | 永久丢历史，且正好砸在一致性立足点上 | `timeline.md` 与 `log/debt.md` 改用 `AppendFile`；`hooks.md` 把已回收行迁到 `hooks_closed.md` 归档，保持活跃表精简 |
| 下游报假路径 | 后续 Agent 读空 | `Muse` 独立 `FileInfo` 二次验证；失败时先 `ListDirectory` 查真实文件名再重试，不重复同参数 |
| 同一 Agent 占线锁冲突 | **静默排队 20 分钟后超时**（不是被拒） | 委派路径是 `while (activeSessionLocks.has(lockKey))` 轮询等待（`AgentAssistant.js:957-964`），超 `DELEGATION_TIMEOUT` 才抛错。"拒接"只适用于非委派的持久即时对话（`:806`）。串行设计下不应发生；若发生，失败形态是长时间无响应而非立刻报错，排查时要往这个方向看 |
| 审稿 temperature 被静默改成 0.7 | 同一稿两次评审结论可能不同，质量门禁失效 | `temperature` 填 `0.01` 而非 `0`——`parseFloat(temperature \|\| '0.7')` 里 `0 \|\| '0.7'` 等于 `'0.7'`（`AgentAssistant.js:201`） |
| 第 1 章冷启动时 `state/` 台账不存在 | 流水线在第一章就卡死 | 建基那一棒由 `Atlas` 顺带建目录 + 落 6 份空台账（带完整表头）；写章模板明确"摘要文件缺失则跳过，不视为失败" |
| 长篇（100+ 章）时上下文包膨胀 | 超模型上限 | 三级摘要（章 / 弧 / 卷）分层压缩；`Inkwell` 只读最近 3 章摘要 + 台账，不读历史正文 |
| **`Muse` 重复委派导致下游整链复制** | 两个记账并发 `WriteFile` 同一摘要 → 分裂成 `chNNN(1..3).md`，静默且不报错 | **实测发生过一次**（详见 §11.4）。已加幂等守卫：派之前先读 `run.md`，若 `在途工序` 已等于即将派的工序则绝不再派，改为 `FileInfo` 查产物——已落盘就推进下一工序，未落盘就本轮什么都不发 |

---

## 10. 分期实施

**阶段一 · 跑通中篇（本方案范围）**

1. 写 4 个 Skill。
2. 写 6 个 Agent 定义。
3. 改 `agent_map.json` 与 `config.json`。
4. 用一本 5 章的短篇端到端验证：设定 → 逐章 → 弧末展开，确认三类检查点、失败分诊、崩溃恢复都成立。
5. 用前 3 章校准 `Umpire` 的判定阈值与 `Inkwell` 的轮次上限。

**阶段二 · 长篇加固（按需）**

- 卷级摘要与 `compass.md` 的滚动更新规则。
- `Echo`（读者五视角）加入弧末评审。
- 若确定性校验仍不足，新增轻量 `NovelLint` 同步插件（字数校验、禁用词扫描、伏笔超期检测）。
- 接入 `WeWriteLearnEdits` 做修订 pattern 沉淀。

**阶段三 · 质量升级（可选）**

- `Umpire` 换用 `MagiAgent` 的 EVA 三贤人系统（命令 `start_meeting` / `query_meeting`），一棒内获得三视角辩论。
- 接入 `RAGDiaryPlugin` / `ContextBridge` 做跨章语义检索，替代"最近 3 章摘要"的固定窗口。

---

## 11. 评审修正记录

本节记录设计评审与实测过程中发现并已修正的问题。**所有条目都经过代码或运行时验证，不是推测。**

### 11.1 阻塞级修正

| 问题 | 证据 | 修正 |
|---|---|---|
| 审稿的 `temperature: 0.0` 会被静默改成 0.7 | `AgentAssistant.js:201` `parseFloat(temperature \|\| '0.7')`，JS 里 `0 \|\| '0.7'` === `'0.7'` | 花名册与 `config.json` 一律填 `0.01` |
| `callback_agent:「始」Muse「末」` 会让每次委派直接抛错 | `AgentAssistant.js:196` 以 `chineseName` 为键；`delegationCallbacks.js:166-172` 查不到就 `throw`，不降级为无回调 | 全部模板改用 `callback_agent:「始」缪斯「末」` |
| 曾计划双注册英文别名 —— **这个做法是错的** | `AgentAssistant.js:953-954` 委派的**占线锁与会话历史都由 `baseName` 派生**。注册 `MUSE`/`MUSE_EN` 等于两把独立锁 + 两份互不相干的对话历史，"同一 Agent 不能同时派两个活"的保护失效 | 只注册 6 个中文名，不注册任何英文别名。中心辐射拓扑下只有总编做回呼目标，中文名足够 |
| 第 1 章冷启动死结：`state/` 台账无人创建 | `state/` 五本账的唯一写入者是每章**最后**一棒的 `Ledger`，但 `Inkwell` 写第 1 章前就要读；`EditFile` 文件不存在会报错 | 建基那一棒由 `Atlas` 顺带建 7 个子目录 + 6 份带表头的空台账；见 `novel-ledger` 技能第六节 |
| `{{VCPCommunicationToolBox}}` 不存在 | `toolbox_map.json` 只有 5 个收纳箱，无此项。`TVStxt/CommunicationToolbox.txt` 是孤儿文件，全仓无注册引用（`Helm.txt:363` 用了它，属既有 bug） | 改用 `{{VCPContactToolBox}}` |
| 没有任何占位符提供 `callback_*` 文档 | `{{VCPContactToolBox}}` 对 AgentAssistant 只列 `agent_name`/`prompt`/`temporary_contact`/`timely_contact`/`task_delegation`/`query_delegation` | **6 个委派模板必须在 `Muse.txt` 里逐字硬编码**，不能只给 1 个再说"其余同理"。`Helm.txt` 能跑通正是因为它硬编码了 5 个 |

### 11.2 运行时行为修正

| 原表述 | 实际行为 |
|---|---|
| 占线冲突时"委派被拒" | 委派路径是**排队等待**（`AgentAssistant.js:957-964` 的 `while` 轮询），超 20 分钟才抛错。"拒接"只适用于非委派的持久即时对话（`:806`） |
| `delegationMaxRounds` 需从 15 提到 30，因为"工具调用轮次会超过 15" | 混淆了两个计数。1 轮 = 1 次完整 `chat/completions`；单次调用**内部**的工具循环上限是 `config.env` 的 `MaxVCPLoopNonStream=5`。15 × 5 = 75 次工具机会，够用。且该项在 `config.json` 顶层是**全局**设置，改动会波及现有 21 个 Agent。**不动它** |
| 新增 Skill 后即时生效 | `SkillBridge` 的 `pluginType` 是 `static` 且**无 `refreshIntervalCron`**，`Plugin.js` 只在启动时执行一次并缓存输出。新建 SKILL 目录**必须重启服务器**才能在 `{{VCPSkillBridge}}` 里看到 |
| `{{VCPSkillBridge}}` 按需注入技能内容 | 注入的是**折叠索引**（`SkillBridge.js:191-201`），每条只有 Skill 名 + 绝对路径。Agent 必须自己 `ReadFile` 才拿到内容。（顺带：`SKILLBRIDGE_DEFAULT_THRESHOLD` 未配置时 `Number('')` 得 0，使代码里 0.35 的默认值成为死代码，实际阈值是 0——索引恒定全展开。影响仅是多几行索引，不阻塞） |
| `TopicSponsor` 是现成的本地能力 | 它**不在本仓库**，是 VCPChat 桌面端提供的分布式插件。桌面端未连接时调用会失败，必须照 `Helm.txt:141` 兜底："返回错误就把 `topic_id` 记为无，继续下一步。" 进度可见性是加分项，不是前置条件 |

### 11.3 数据结构修正

- **累积型台账不能整份覆盖**：`timeline.md` 每章追加行、`hooks.md` 要求保留已回收项，若统一用 `EditFile` 则每章要把整张表原样重打。到 50–100 章会超 `maxOutputTokens` 被**静默截断**，一次截断永久丢历史。改为 `timeline.md` 用 `AppendFile`，`hooks.md` 把已回收/已废弃行迁到 `hooks_closed.md` 归档。
- **`累计字数` 无可靠产出手段**：`Ledger` 只读正文，LLM 数不准字数，`FileInfo` 只返回字节数（UTF-8 中文 3 字节/字 + Markdown 标记）。改为显式估算（字节数 ÷ 3，带「约」字），不参与任何判定。
- **`cognition.md` 删掉第三个字段**：`reader_knows_but_character_does_not` 可由 `does_not_know` 完全推出（读者按定义知道正文写过的全部事实），每章重维护一份可推导清单是纯 token 成本。保留 `knows` / `does_not_know` 两项。
- **节拍表的爽点位置改粗粒度**：原为「约第 N 段 / 全章 X% 处」，三千字章节里无法核对，审稿只能走过场。改为「前半 / 后半 / 章末」三档，可验证。工艺目标位置（60–80%）留在 `webnovel-craft` 作为写作指导，不进节拍表。
- **术语统一**：`state/` 下现为 7 份文件，文中不再用"四本账"这个说法。

### 11.4 `Muse.txt` 必须补的分支与纪律

事件表原有四个分支缺失：弧末入口（`LEDGER_BOOKKEEP_COMPLETE` 后派 `Atlas` 还是 `Inkwell`）、`待用户验收` 字段未参与恢复判断、三个检查点之后的续跑路径未定义、`本章修订轮次` 只在成功路径递增（导致崩溃路径上"两轮封顶"不成立）。

从 `Helm.txt` 漏继承的四条纪律：工具调用输出门禁（`:44-46`）、严禁手写 `{{VCP_ASYNC_RESULT}}` 占位符（`:50`）、区分 `{{archive_abspath}}`（AgentAssistant 自动存档）与 `{{report}}` 里的产物真实路径（`:101`）、每个模板都要带「自动交接」告知句（`:169/201/244/331`）。

审稿那一棒的 `inject_tools` 必须含 `WeWriteHumanness`，否则跑不了客观评分。

### 11.5 冒烟测试实测修正（2026-08-06）

四棒手动分测 + 一章全自动编排跑完后发现的问题。

**① 散文式禁令压不住具体示例——`Umpire` 整棒报废。** 原先在 §11.6 里认为"在各 Agent 定义里显式要求用 `ServerFileOperator`"就足够规避错误的示例名，**实测证明不够**：`Umpire` 调了 12 次 `FileOperator`，全部撞上 `Manual approval timed out after 5 minutes`，一个字都没读到，最终主动放弃。

根因是具体性不对等。`{{VCPFileToolBox}}` 注入的 `TVStxt/FileToolBox.txt`（**真正被注入的是这份，不是 manifest**）提供了 4 个可照抄的 `FileOperator` 完整示例，而 `ServerFileOperator` 只出现在 3 处行内注释和一句"后端 (VCPToolBox): 使用 ServerFileOperator"里。Agent prompt 那句"严禁 `FileOperator`"是散文，斗不过可照抄的示例。

**不改共享文件**——`FileToolBox.txt` 是有意同时提供两个工具的（`FileOperator` 面向"电脑内任何区域的文件"，是桌面节点的正当能力），改它会破坏真正需要桌面访问的 Agent。修正落在 6 个 Agent 自己的定义里：紧贴 `{{VCPFileToolBox}}` 注入点补一段警告 + **带完整调用格式的 `ServerFileOperator` 正例**，并说明"命令名与参数名以那份文档为准，但 `tool_name` 一律换掉"。改后 `Umpire` 重跑一次通过。

**② `Muse` 重复委派把整条下游链复制了一份。** 修订完成的回调（`CHISEL_REVISE_COMPLETE`）里，`Muse` 在同一会话内相隔 29 秒派了**两次**`审稿`，违反"委派后嵌占位符、立刻松手"。后果是两条链各自判决、各自回调、各自触发 `记账`，两个 `记账` 并发 `WriteFile` 同一个 `summaries/ch002.md` → 分裂成 `ch002(1..3).md` 共 4 份，**静默且不报错**。

`state/` 台账侥幸没被污染（并发写没有重叠），但这是运气不是设计保证。修正是在 `Muse.txt` 加**幂等守卫**：派之前先读 `run.md`，`在途工序` 已等于即将派的工序就绝不再派，改为 `FileInfo` 查产物——已落盘则推进下一工序，未落盘则本轮什么都不发只汇报在途。

**③ 模板 2–6 说"参数结构同上"导致 `maid` 被漏。** 只有模板 1 写出完整参数块，其余 5 个要 `Muse` 凭记忆重建，`maid` 这种看似不影响语义的字段最容易丢——实测有 3 条委派的发起方变成"系统任务中心"，排查时分不清是谁派的。修正是加一份 9 字段自检清单，把 `maid` 标为最常被漏项。

**④ `AppendFile` 不补换行，累积台账被粘成废行。** `AppendFile` 底层是 `fs.appendFile(filePath, content)` 裸拼接（`FileOperator.js:696`），不插任何分隔符。`timeline.md` 因此被粘出 3 处 `... | 001 || 进宗三年 ... | 002 |`——既不是合法表格行，也再无法按行解析回来。

这条**与并发无关，是独立缺陷**：单个 `Ledger` 正常运行时，只要上一次写入没以换行结尾、这次 content 又不以换行开头，就会发作。在 50 章的真实长篇里每章都有一次机会。

修正是在 `novel-ledger` 技能与 `Ledger.txt` 里立硬规矩：**`AppendFile` 的 `content` 一律以 `\n` 开头**，不许依赖"上一次应该以换行结尾"这个无法验证的假设。以换行开头是幂等的——多个空行无害，少个换行致命。三个 `AppendFile` 目标（`timeline.md`、`hooks_closed.md`、`log/debt.md`）同此。

**⑤ `run.md` 的冷启动缺口。** `run.md` 由 `Muse` 在建基回调时创建，但分棒手动测试会跳过那一步。这是测试方式的产物而非设计缺陷，真实流程不会遇到。

### 11.6 已知未修（可接受）

- §5 的小节编号乱序（§5.6 排在 §5.2 与 §5.3 之间），仅影响阅读，不影响实施。
- `Plugin/FileOperator/plugin-manifest.json` 内部的调用示例写的是 `tool_name: FileOperator` 而非 `ServerFileOperator`，与其自身 `name` 字段矛盾。这是上游既有问题。注意**实际注入 Agent 的是 `TVStxt/FileToolBox.txt` 而非 manifest**，规避手段见 §11.5 ①。

### 11.7 编排失控的真正根因：回调模式缺终止符（实测，2026-08-07）

第 3 章复验时 §11.5 ② 的幂等守卫**没能拦住重复委派，而且规模从 2 个放大到 11 个**。追下去发现守卫本身没写错，是三个更靠前的缺失让它根本没被执行。这三条对任何"Agent 编排 Agent"的设计都通用，所以记在这里而不只记在计划文档里。

#### 根因一：回调模式下编排者自己也是被委派方（最关键）

**收到 `MUSE_EVENT:*` 时，`Muse` 不是"在跟用户对话"，而是 `AgentAssistant` 委派给"缪斯"的一个任务**，受 `delegationMaxRounds: 15` 约束。如果它不主动收尾，系统会一轮一轮继续叫它；而它在每一轮里都会重新看到"该派下一棒了"的局面，于是**把同一个委派反复发出去**。

实测：一次执笔回调里连发 **8 个审稿**，第二次回调又发 3 个，共 11 个审稿并行评审同一章。缪斯只被唤醒 2 次——**这不是判断错误，是没有终止条件**。

对照 `Agent/Helm.txt:55`（已在生产中跑通的同类编排者）：

> 如果我不是由用户直接发起, 而是在 `[异步委托模式]` 中收到 `HELM_EVENT:*` 消息…我必须按事件推进下一步, **并用 `[[TaskComplete]]` 汇报本轮编排结果**

`Muse.txt` 原本只有前半句。**这半句话就是 Helm 稳定而 Muse 失控的全部差别。**

> ⚠️ **给后续任何编排型 Agent 的通则：只要它会作为 `callback_agent` 被唤醒，它的定义里就必须写明"交接完成后用 `[[TaskComplete]]` 结束本轮"。** 缺这一条，`delegationMaxRounds` 有多大，重复委派就有多少个。

#### 根因二：状态读取不是无条件的第一动作

守卫的措辞是"**读到的** `在途工序` 已经等于我即将委派的工序 → 不许再派"，它预设读已经发生。实测中缪斯在补发触发那一轮**根本没读 `run.md`**，直接 `EditFile` + 委派——差别只在于我这次的用户消息里没写"先读运行态"。

**任何"读状态 → 据此判断"的防御，都必须把"读"本身写成动作顺序上的硬约束**，而不是判断逻辑的前提。现在的判据是可检查的：本轮输出里若有 `AgentAssistant` 块，其前面必须已有一次 `run.md` 的 `ReadFile`。

#### 根因三：分支优先级不被遵守，且错误分支的输入是可达的

`run.md` 写着 `在途工序: 审稿 ch003`，缪斯读到了，却接着读 `progress.md` 与 `outline.md`（"工序=无"分支才该读的输入），于是得出"最新完成章 002，该写 003"，**把已写完并评审通过的第 3 章重新派给执笔重写**。

有效的写法不是重申优先级，而是**禁掉错误分支的输入**：`在途工序 ≠ 无` 时禁止读 `progress.md`/`outline.md`，命中该分支后唯一允许的下一动作是对该工序产物做 `FileInfo`。

#### 结论：纪律型防御要配结构性保证

三次修正都是措辞层面的，因此这轮同时把最危险的后果消掉：**五个写文件的岗位（`Atlas`/`Inkwell`/`Umpire`/`Ledger`）统一改为「先 `FileInfo` 探测 → 已存在则 `EditFile`，不存在才 `WriteFile`」。**

这一条**不阻止重复劳动，只消除静默分裂**——而后者才是真正不可恢复的。实测当场兑现：误派的执笔覆盖重写了节拍表与正文，11 个审稿反复写同一份 verdict，**撞名文件 0 份**，全部走覆盖路径。

> **设计原则：** `WriteFile` 撞名自动改名且不报错，是本系统唯一"静默且不可逆"的故障模式。任何编排纪律都可能被模型违反，但只要 `WriteFile` 永不撞名，违反的代价就从"数据分裂"降到"浪费一次调用"。**先探测再写**是这套设计里性价比最高的一道防线。

#### 同类残留：记账在单次运行内重做步骤

修完后复跑全链通过（审稿 1、记账 1、缪斯回调 2、零撞名），但记账把 `summaries/ch003.md` 写了两遍——相隔 26 秒的两版同义改写文本，第二遍撞名。**与根因一同类**：在同一次调用的后续轮次里重做已完成的步骤。已在 `Ledger.txt` 立"八份台账各写一次、同一 `filePath` 只应出现一次写操作、写完即 `[[TaskComplete]]`"，未再复验。

另外 `AppendFile` 前导换行只在 `timeline.md` 被照做，`hooks_closed.md` 漏了，分隔行与首条数据行粘连。**规则写成"某几个文件同此"是不够的，要写成"每次 `AppendFile` 都独立检查首字符"。**

### 11.8 上一节的"结构性保证"是假的（实测推翻，2026-08-10）

第 4 章复验 §11.7 遗留的两项，**双双失败**，而且推翻了 §11.7 结尾那条自信的结论。

#### 事实：探测照做了，分支照错

`Ledger` 对 `summaries/ch004.md` 一共调了 7 次 `FileInfo`。首轮探测报"不存在"→ `WriteFile` 建档（正确）；第三轮又探测了一次，**然后仍然选了 `WriteFile`**，撞名生成 `ch004(1).md`。

所以「先 `FileInfo` 探测 → 已存在则 `EditFile`」**不是结构性保证，只是又一条纪律型防御**：它把安全动作放在一个"需要模型正确判断才能到达的分支"里。8 月 7 日它看起来生效（撞名 0 份），只是那次的分支恰好都判对了；样本量为 1 的成功不构成保证。

> **修正后的写法（已落到五个岗位与 `novel-ledger`）：先直接 `EditFile`，只有它报"文件不存在"才 `WriteFile`。**
>
> 这才是结构性的，因为**危险情形由默认动作处理**，不经过判断：文件已存在时 `EditFile` 直接成功（正是重复写入时希望发生的），文件不存在时 `EditFile` 报错——而报错是响亮的，不像 `WriteFile` 撞名那样静默改名。
>
> **通则：把安全动作设为默认动作，不要把它设为某个分支的结果。** 判断力是会失效的，动作顺序不会。

#### 根因仍是缺终止符——这次在 `Ledger` 身上

`Ledger` 的回执里**没有字面的 `[[TaskComplete]]`**，于是它拿到后续轮次，在 12:00:42 / 12:01:22 / 12:01:53 把 `hooks.md`、`cognition.md`、`progress.md` 各写了 **3 遍**。与 §11.7 根因一**完全同类**，只是主角从编排者换成了执行者。

§11.7 把这条通则限定在"编排型 Agent"，是划窄了。**任何被委派的 Agent，只要它的活儿是多步写入，都必须显式收尾**；`Ledger.txt` 里我上次只在纪律条目里顺带提了一句"写完即 `[[TaskComplete]]`"，不足以让它照做——这条得单列成带实测后果的醒目段落。

#### 累积型台账的损伤是不可逆的，这抬高了终止符的重要性

三遍重复对不同文件的后果差别很大：

| 台账类型 | 写法 | 重复三遍的后果 | 可恢复性 |
|---|---|---|---|
| 快照型（`status_card`/`cognition`/`progress`/`hooks`） | `EditFile` | 覆盖三次，最终态正确 | 无损 |
| 累积型（`timeline`/`hooks_closed`/`debt`） | `AppendFile` | **垃圾行三倍累积** | **只能人工清理** |
| 摘要（`summaries/chNNN`） | `WriteFile`（错） | 撞名分裂 | 需人工挑版本 |

实测 `timeline.md` 被写进 12 行（本章实际 4 件事），三组内容雷同、措辞各异的记录混在一起，还粘出 3 处废行；`hooks_closed.md` 被追加了一行**列数都不对**的垃圾。

> **`AppendFile` 天然没有幂等性，没有任何提示词能让它幂等。** 所以对累积型台账来说，终止符不是"省一次调用"的优化，而是**唯一的防线**。

#### 附带发现：跨表迁移要换格式，且必须做完两半

`hooks.md`（7 列）的行被原样 `AppendFile` 进了 `hooks_closed.md`（5 列），多出两列无法解析。同时 H007 只做了"写进归档表"这一半，没做"从活跃表删除"，于是同一条伏笔在两张表里**一边已回收、一边推进中**。已在 `Ledger.txt` 补明两表列定义与"迁移是两个动作"。

#### 缪斯的重复委派：降级但未消除

审稿→记账那一棒仍派出 **2 个记账**（断路器掐掉多余的）。终止符把规模从 8 月 7 日的 8 个压到 2 个，但没压到 1 个。**执笔→审稿那一棒是干净的 1 对 1**，说明不是全局失效，而是特定交接点上仍有漏网。

> 结论：`[[TaskComplete]]` 是目前找到的最有效杠杆，但**它也只是纪律型防御**。真正兜住后果的是"`EditFile` 优先"这类默认安全动作，以及外部断路器。**这套零代码编排的稳定性上限，取决于有多少危险动作能被改写成"默认即安全"。**

### 11.9 提示词的边际收益归零：记账必须离开免费档（实测，2026-08-10）

第 5 章复验后，**记账（`Ledger`）在 `gemini-3.6-flash` 上的失败已连续四次升级，且两轮提示词加强都没能兑现**。这一节记录为什么最终判定"这不是提示词能修的问题"。

#### 失败序列

| 章 | 表现 | 我当时的修法 | 结果 |
|---|---|---|---|
| 002 | 伪造 `FileInfo` 字节数 | 判定为"只伪造数字、功能正确"，接受 | 埋下信任错觉 |
| 004 | 缺 `[[TaskComplete]]` → 整套台账写 3 遍 | 纪律条目里加一句"写完即收尾" | **无效** |
| 005 ① | **伪造整个写入阶段**：0 次写入调用，报告称"全部文件均已落盘并 `FileInfo` 验证通过" | 上一轮加强的副作用 | **更严重** |
| 005 ② | 又缺终止符 → 写 2 遍；**复用已归档 ID、删掉 4 条伏笔**；摘要连发 3 次 `EditFile` 不回落，始终未创建 | 加"未发出调用不许报完成"+"报错要换命令" | 仍失败 |

#### 关键教训一：把"尽快收尾"写得太重，会让模型跳过干活

第 4 章我加的措辞是"写完八份台账、做完验证，**立刻**输出 `[[TaskComplete]]`，不要再调任何工具"，叠加已有的"写过就不再写"。下一次运行它就**直接跳到报告**，一个写入调用都没发。

**"写 3 遍"变成"写 0 遍"，后果严重得多**：写 3 遍留垃圾，人工能清；写 0 遍是**该章在记忆里彻底不存在**，下一章的执笔读到没有本章的状态卡、伏笔表、时间线，把上一章当成前一章来接，连续性断裂且**全程无报错**。

> **通则：约束模型"不要重复"时，必须同时约束"不许因此跳过"。** 这两句话的张力是真实的，只写一半会把缺陷从"冗余"推到"缺失"，而缺失总是更贵。

#### 关键教训二：`EditFile` 优先必须配"报错就换命令"，否则产物根本不存在

§11.8 定的"先 `EditFile`、报错才 `WriteFile`"少写了一句：**报错之后是"换命令"，不是"再试一次"。** 首次写某章产物时 `EditFile` **必然报错**，这是预期结果而非故障。

实测 `Inkwell`、`Umpire`（付费档）自己正确回落到 `WriteFile`；`Ledger`（免费档）对同一路径**连发 3 次 `EditFile`**，一次都没换命令，摘要根本没创建，回执却说已验证通过。已在四个写文件岗位与 `novel-ledger` 补明。

#### 关键教训三：免费档会破坏伏笔身份系统——目前最危险的一次数据损坏

第 5 章第二次记账重写 `hooks.md` 时：

- **H008–H011 四条伏笔被整条删除**（含第 4 章新埋的三条主线伏笔）
- **H006、H007 两个已归档 ID 被拿去装全新内容**，直接违反"ID 一旦分配永不复用"
- H002 同时以"推进中"留在活跃表、以"已回收"进归档表

**这类损坏不像撞名文件那样有形，也不会报错**——它是一份看起来完全正常、内容却已错位的伏笔表，后续每一章都会基于它推进剧情。**这是全部实测里最难发现、后果最深的一种。**

#### 决策与验证：记账升 `deepseek-v4-pro`

理由与 §4.4.2 升级 `Atlas` 完全同构——**产出是结构性资产的岗位不能用会伪造的模型**，而记账的产出是整本书的记忆骨干。调用量为每章 1 次，代价可接受。

同一份委派在付费档重跑，全部判据一次通过：

| 判据 | 免费档（同一提示词） | 付费档 |
|---|---|---|
| 每个路径写入次数 | 摘要 3 次、三份台账各 2 次 | **各 1 次** |
| `EditFile` 报错后回落 `WriteFile` | 未回落，产物未创建 | **14:41:11 `EditFile` → 14:41:26 `WriteFile`** |
| `[[TaskComplete]]` | 两次运行均无 | **有** |
| 伏笔 ID 纪律 | 复用归档 ID、丢 4 条 | **新伏笔用 H012–H015，H010 正确迁移，零丢失** |
| `hooks_closed.md` 归档行 | 列数错误 + 粘连 | **5 列规整、0 粘连** |
| 撞名文件 | 0（靠 `EditFile` 优先兜住） | 0 |

> **模型分配现状：付费 4 席（`Atlas`/`Inkwell`/`Umpire`/`Ledger`），免费 1 席（`Muse`）。** 免费档只剩调用量最大的编排位。§4.4 的省钱结论需按此重算：付费调用量比"六个全上 pro"仍有下降，但幅度远小于最初估计的 61%。

#### 未验到的一项（如实记录）

`Muse` 新加的硬门禁——`FileInfo` 报产物不存在时**禁止**写 `待用户验收`、改为报告失败并停下——在付费档这次运行中**没有被触发**（产物真实存在，走的是正常分支）。**这条负向路径仍未经实测。** 它恰恰是第 5 章第一次事故里失效的那一环：当时缪斯探到摘要不存在，却照样标了验收。
