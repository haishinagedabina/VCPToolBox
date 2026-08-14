# 小说撰写团队 实施计划

> **For agentic workers:** 用 superpowers:subagent-driven-development 或 superpowers:executing-plans 逐任务实施。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 在 VCPToolBox 内建成 6 个 Agent 的中文网文撰写流水线，能从一句话设定产出逐章成稿并保持长程一致性。

**Architecture:** 纯 Agent 编排。`Muse` 作中心辐射枢纽，用 `AgentAssistant` 的 `task_delegation` + `callback_agent` 串行调度 5 个工种 Agent；所有状态沉盘到 `file/novel/<book-id>/` 黑板，每个文件单一写入者；创作规则放 `SkillBridge` 的 4 个 Skill 按需披露。零新增插件代码。

**Tech Stack:** Agent 定义 = `Agent/*.txt`（提示词）· 技能 = `Plugin/SkillBridge/SKILL/*/SKILL.md` · 注册 = `agent_map.json` + `Plugin/AgentAssistant/config.json` · 黑板 IO = `ServerFileOperator` · 客观校验 = `WeWriteHumanness`

**设计依据:** `docs/superpowers/specs/2026-08-05-novel-writing-team-design.md`

---

## 当前进度

| 任务 | 状态 |
|---|---|
| Task 1 · Skill `novel-ledger` | ✅ 已完成，SkillBridge 已收录 |
| Task 2 · Skill `webnovel-craft` | ✅ 已完成，SkillBridge 已收录 |
| Task 3 · Skill `story-architecture` + 仙侠 preset | ✅ 已完成，SkillBridge 已收录 |
| Task 4 · Skill `manuscript-review` | ✅ 已完成，含 `WeWriteHumanness` 实测适配（见 spec §7.1） |
| Task 4b · 解开冷启动死结 | ✅ 技能侧（`novel-ledger` 第六节）+ Agent 侧（`Atlas.txt` Step A3）均已完成 |
| Task 5 · 五个工种 Agent 定义 | ✅ 已完成，`agent_map.json` 热重载已确认 21→27 |
| Task 6 · 注册 6 个 Agent | ✅ 已完成，四层验证通过；服务器已重启，`{{VCPSkillBridge}}` 已含 16 个 Skill |
| Task 7 · `Muse.txt` 总编 | ✅ 已完成，含 6 个完整委派模板 |
| Task 8 · 端到端验证 | 🟡 五章全链已跑通；第 5 章 ⑤⑥ **通过**，编排层零重复委派。代价是**记账升 `deepseek-v4-pro`**——免费档两次失守（伪造整个写入阶段、破坏伏笔 ID 系统），提示词边际收益归零。现付费 4 席、免费 1 席（仅缪斯）。**待验：缪斯硬门禁的负向路径（至今未触发）** |

### 冒烟测试结论（`_smoke` 丢弃书，2026-08-06）

四棒手动分测（建基 → 写作 → 评审 → 记账）与缪斯自主编排的第 2 章全链**都跑通了**：委派模板、回调链、`WeWriteHumanness` 六维评分、台账刷新全部按设计工作。产物 21 份，现已清理至 0 撞名。

同时暴露四个缺陷，均已修正但**只有前两项验证过运行时生效，纠错效果本身未经实测**：

| # | 缺陷 | 修正 | 状态 |
|---|---|---|---|
| ① | 6 个 Agent 照抄注入文档里的 `FileOperator`，`Umpire` 整棒报废 | 各 Agent 定义里补 `ServerFileOperator` 正例 + 警告 | ✅ 已实测通过 |
| ② | 免费 flash 档伪造 `FileInfo` 字节数 | `Atlas` 升 `pro`；高频岗位靠下游 `ENOENT` 兜 | ✅ 已实测确认边界 |
| ③ | 缪斯重复委派，下游整链被复制，摘要分裂成 4 份 | `Muse.txt` 加幂等守卫 + 9 字段自检清单 | ❌ **复验失败**，根因更深，见下 |
| ④ | `AppendFile` 不补换行，`timeline.md` 被粘出 3 处废行 | 技能与 `Ledger.txt` 立规矩：content 一律以换行开头 | 🟡 `timeline` 通过，`hooks_closed` 漏了 |

细节见 spec §11.5。

### 第 3 章复验结论（2026-08-07，额度已恢复）

`gemini-3.6-flash` 探针 3/3 通过后跑了第 3 章。**幂等守卫复验失败，且失控规模比上一次大 5 倍**——但恰恰因此定位到了真正的根因，最终修完后全链复跑一次通过。

#### 失败经过：单次唤醒发出 11 个审稿

| 阶段 | 观察 | 结论 |
|---|---|---|
| 用户消息启动第 3 章 | 新建委派恰好 1 条，`sender='缪斯'`，8 个必需字段齐全，先写 `在途工序` 再委派 | ✅ 顺序与 `maid` 修正生效 |
| 执笔在途时补发相同触发 | **又派了一个执笔**。缪斯本轮**根本没读 `run.md`**，直接 `EditFile` + 委派 | ❌ 守卫未执行 |
| 执笔回调后 | 缪斯只被唤醒 2 次，却派出 **11 个审稿**（第一次唤醒 8 个，第二次 3 个） | ❌ 失控 |

三个独立缺陷叠在一起，都不是"守卫判错"，而是**守卫根本没被执行**：

1. **读 `run.md` 不是无条件第一动作。** 启动那次我在用户消息里写了"先读运行态"，它就读了；补发那次没写，它就没读。守卫拿不到读数，条件永不成立。
2. **回调路径缺终止符（真正的大头）。** 收到 `MUSE_EVENT:*` 时缪斯自己就是被委派方，`delegationMaxRounds: 15` 给它 15 轮，而 `Muse.txt` 从没要求它用 `[[TaskComplete]]` 收尾。于是它把剩余轮次全填上了同一个委派。对照 `Helm.txt:55` 明确写着"按事件推进下一步，**并用 `[[TaskComplete]]` 汇报本轮编排结果**"——缪斯漏的正是这后半句。
3. **分支选择不遵守优先级。** 读到 `在途工序: 审稿 ch003` 后，它继续读了 `progress.md` 与 `outline.md`（那是"工序=无"分支的输入），得出"最新完成章 002，该写 003"，把已写完并评审通过的第 3 章**重新派给执笔重写**。

#### 修正：三条机械约束 + 一条结构性保证

纪律型措辞在这一档模型上已连续失手，所以这轮同时上结构性保证：

| 修正 | 落点 | 性质 |
|---|---|---|
| 第一动作门禁：有 `AgentAssistant` 块则其前必须已有 `run.md` 的 `ReadFile` | `Muse.txt` | 动作顺序约束 |
| 回调模式必须以 `[[TaskComplete]]` 收尾，并给出一轮的六步完整形状 | `Muse.txt` | **补终止符（关键）** |
| 「一轮一个」≠「每轮一个」：整次唤醒总共只发一个委派 | `Muse.txt` | 消除误读 |
| `在途工序 ≠ 无` 时**禁止**读 `progress.md`/`outline.md`，附四行动作表 | `Muse.txt` | 分支互斥 |
| 五个写文件岗位统一「先 `FileInfo` 探测 → 存在则 `EditFile`，不存在才 `WriteFile`」 | `Atlas`/`Inkwell`/`Umpire`/`Ledger` + `novel-ledger` | **结构性：撞名从静默分裂变成确定性覆盖** |

**结构性保证当场兑现：** 误派的执笔重写了 `ch003.plan.md`（3484→5017B）与 `ch003.md`（11346→14335B），11 个审稿也反复写同一份 verdict，**撞名文件 0 份**——全部走 `EditFile` 覆盖，没有再生出 `ch003(1).md`。这一条不阻止重复劳动，但阻止了唯一真正危险的后果：静默数据分裂。

#### 修完后复跑：全链通过

| 判据 | 结果 |
|---|---|
| 读 `run.md` 为第一动作 | ✅ |
| `FileInfo` 查判决（不存在）→ 重派审稿而非重写正文 | ✅ 分支约束生效 |
| 违规读 `progress.md`/`outline.md` | ✅ 无 |
| 委派数 / 对象 | ✅ 1 个「审稿」 |
| 输出含 `[[TaskComplete]]` | ✅ |
| 回调链委派计数 | ✅ 审稿 1、记账 1、缪斯回调 2，断路器零触发（上一轮同位置 11 个） |
| `timeline.md` 第 3 章新增 4 行 | ✅ 0 粘连，每行恰好 5 个 `\|` |

#### 复跑仍暴露两处（→ 第 4 章复验，**两项均失败**，详见下节）

| # | 缺陷 | 修正 |
|---|---|---|
| ⑤ | 同一次记账把 `summaries/ch003.md` **写了两遍**（内容为同义改写的两版，相隔 26s），第二遍撞名生成 `ch003(1).md` | `Ledger.txt` 纪律 10：八份台账各写一次，同一 `filePath` 只应出现一次写操作；写完即 `[[TaskComplete]]` |
| ⑥ | `AppendFile` 前导换行只在 `timeline.md` 照做，`hooks_closed.md` 漏了，分隔行与首条数据行粘成 `\|---\|---\|---\|---\|---\|\| H003 \| ...` | `Ledger.txt` 与技能均改为「三个 `AppendFile` 目标每次独立检查首字符」 |

⑤ 与缪斯那个 11 连发是同一类根因：**在同一次调用的后续轮次里重做已完成的步骤**。缪斯靠 `[[TaskComplete]]` 解决，记账用同一思路——但**第 4 章复验证明这轮修正不够**（纪律条目里顺带提一句，记账不会照做），已改为单列醒目段落。

#### 黑板现状（已清理，可继续）

`run.md` = `在途工序: 无` / `待用户验收: ch003`；`progress.md` 记 3 章约 11100 字；`state/` 恰好 7 份、撞名 0；`hooks.md` 6 条活跃（H003/H006 已正确归档进 `hooks_closed.md`，粘连已修）；`timeline.md` 13 行、0 粘连。作废的旧 verdict（针对被覆盖前的正文）已删，现存 verdict 对应当前正文。

**成本提示：** 这一轮失控又烧掉一批免费额度。缺陷 ③ 的每次复发都同时是数据事故与成本事故。

### 第 4 章复验结论（2026-08-10，⑤⑥ 双双失败）

三档模型全部可用（各探 2 次全 200）。起点干净，全链 19.4 分钟跑完，四份产物齐全，第 4 章判定 PASS（最低维度 5 分，文体校正 AI 味分 ≈46.46）。**但要验的两项都没通过。**

#### 判据方法的一处踩坑：DebugLog 查不到下游 Agent

上次留的清单让我去翻 `DebugLog/.../LogOutputAfterProcessing-*.txt`。**这条路走不通**——`AgentAssistant` 内部的模型调用不经过服务器主 `/v1/chat/completions` 管线，那批日志只记录我直连缪斯的那一次（本次全链只新增 9 份）。

**正确的数据源是 `DebugLog/ServerLog.txt` 里的 `[PluginManager] Calling local executePlugin for: ServerFileOperator with prepared param: {...}`**，它逐次记录了命令名、`filePath` 与时间戳，是工具调用的地面真相。

#### ⑤ 失败：整套台账被写了 3 遍

日志里的真实时序：

| 时间 | 动作 |
|---|---|
| 12:00:42 | `FileInfo` 报不存在 → `WriteFile` 建 `summaries/ch004.md`（正确） |
| 12:00:42 | `EditFile` `hooks.md` / `cognition.md` / `progress.md` |
| 12:01:22 | 同上三份**再写一遍** |
| 12:01:53 | 同上三份**第三遍**；`FileInfo` 之后**仍选 `WriteFile`** → 撞名 `ch004(1).md` |

**根因：记账的回执里没有字面的 `[[TaskComplete]]`**，于是拿到后续轮次、每轮重做一遍。与第 3 章缪斯失控**完全同类**，只是主角从编排者换成执行者。上次我把这条通则限定在"编排型 Agent"，划窄了。

损伤（`AppendFile` 无幂等性，**不可逆，只能人工清理**）：`timeline.md` 被写进 12 行（本章实际 4 件事）+ 3 处粘连；`hooks_closed.md` 多一行列数都不对的垃圾；摘要撞名分裂。

#### ⑥ 失败：归档行连列数都不对

`hooks.md`（7 列）的行被原样 `AppendFile` 进 `hooks_closed.md`（5 列），既粘连又多两列。且 H007 只做了"写进归档表"，没做"从活跃表删除"，同一伏笔**一边已回收、一边推进中**。

#### 推翻了上次的"结构性保证"

**「先 `FileInfo` 探测 → 已存在则 `EditFile`」不是结构性保证。** 探测确实调了 7 次，第三轮照样选 `WriteFile`——它把安全动作放在"需要判断才能到达的分支"里。8 月 7 日看起来生效，只是那次分支恰好判对了。

已改为**「先直接 `EditFile`，只有它报'文件不存在'才 `WriteFile`」**，五个写文件岗位与 `novel-ledger` 全部同步。理由与通则见 spec §11.8。

#### 稳定性对照：终止符有效但未彻底

| 项 | 第 3 章 | 第 4 章 |
|---|---|---|
| 执笔→审稿 | 1 → 11 个审稿 | **1 → 1，干净** |
| 审稿→记账 | — | 1 → **2 个记账**（断路器掐掉多余） |
| 缪斯先读 `run.md` | 未做 | **已做** |
| 撞名文件 | 0 | 1（记账所致） |

终止符把规模从 8 压到 2，没压到 1。**它是目前最有效的杠杆，但仍是纪律型防御。**

#### 本次已修（均已热重载验证生效）

- `Ledger.txt`：`[[TaskComplete]]` 单列成带实测后果的醒目段落；两表列定义 + "迁移是两个动作"
- `Ledger`/`Inkwell`/`Umpire`/`Atlas` + `novel-ledger`：写法改为 `EditFile` 优先
- 黑板已修复：`timeline.md` 第 4 章 12 行 → 4 行、0 粘连；`hooks_closed.md` 5 列规整；`hooks.md` 移除 H007；撞名文件移入 `_backup_0810_1212/`

### 第 5 章复验结论（2026-08-10，⑤⑥ 通过，代价是记账升付费档）

#### 编排层：全部通过

**零重复委派**——执笔 1、审稿 1、记账 1，缪斯 3 次回调，断路器零触发。第 4 章还漏网的"审稿→记账派 2 个"这次也干净了。缪斯每次都先读 `run.md`，回调均含 `[[TaskComplete]]`。**`EditFile` 优先在执笔与审稿身上按设计工作**（审稿回执原话："EditFile 报不存在 → WriteFile 成功 → FileInfo 验证通过"）。

#### 记账层：免费档彻底失守，两次

| 次 | 表现 |
|---|---|
| ① | **0 次写入调用**，却交出"全部文件均已落盘并 `FileInfo` 验证通过"的报告。缪斯探到 `summaries/ch005.md` 不存在，**照样标了"待用户验收"** |
| ② | 加了反伪造门禁后真的写了（8 分钟），但仍缺终止符 → 写 2 遍；**复用已归档 ID、删掉 H008–H011 四条伏笔**；摘要连发 3 次 `EditFile` 不回落，始终未创建 |

两轮提示词加强都没兑现，且第一轮加强（强调"立刻收尾"）**把"写 3 遍"推成了"写 0 遍"**。判定为提示词边际收益归零，**记账升 `deepseek-v4-pro`**（与 `Atlas` 同理），缪斯留在免费档先验新门禁。

#### 付费档重跑：全部判据一次通过

五个路径各写 1 次；`EditFile` 报错后正确回落 `WriteFile`（14:41:11 → 14:41:26）；`[[TaskComplete]]` 有；新伏笔用 H012–H015 无 ID 复用、H010 正确迁移、零丢失；`hooks_closed.md` 归档行 5 列规整 0 粘连；撞名 0。

#### 本次已修（均已热重载验证）

- `Ledger.txt`：加"没发出写入调用就不许报完成"自查（列出本轮实际发出的写入调用，清单外的文件不许写进"已落盘"）
- 四个写文件岗位 + `novel-ledger`：补"报错之后是**换命令**，不是再试一次"
- `Muse.txt`：加硬门禁——`FileInfo` 报产物不存在时禁止写 `待用户验收`，改为报告失败并停下
- 模型分配：记账 `gemini-3.6-flash` → `deepseek-v4-pro`。现为付费 4 席、免费 1 席（仅缪斯）
- 黑板已回退坏版本并用付费档重记：时间线 001-005 共 23 行 0 粘连，伏笔活跃 11 条、归档 4 条

#### 下次接续（第 6 章）

1. **验缪斯硬门禁的负向路径**——这条是第 5 章第一次事故的失效环节，至今未被触发过。可人为制造一次：删掉某章摘要后让缪斯走记账回调，看它是否拒绝标验收并停下报告。
2. **验缪斯在免费档的稳定性**：它现在是唯一的免费档岗位，且是唯一有"伪造"前科而仍在位的角色。
3. 顺带复看付费档记账在第 6 章是否保持"各写一次"。

#### 台账末尾换行（已知未修，可接受）

付费档记账写完后六份台账**末尾均无换行**。当前不构成风险——`AppendFile` 的前导换行规矩已是硬性要求，实测 0 粘连。留作观察项，不改。

### 占位符交叉核对（建议每次新增 Agent 都跑）

评审在既有 `Helm.txt` 里抓到了 `{{VCPCommunicationToolBox}}` 这个**根本不存在**的占位符（`toolbox_map.json` 里没有，`TVStxt/CommunicationToolbox.txt` 是无人引用的孤儿文件）。这类错误不会报错，只会静默注入空字符串。

防它的办法是把新 Agent 文件里的 `{{Xxx}}` 全部提取出来，与 `toolbox_map.json`、`agent_map.json` 和系统内置变量集求差集。六个新文件已核对通过，用到的只有 `{{VCPFileToolBox}}`、`{{VCPContactToolBox}}`、`{{VCPSkillBridge}}`、`{{Date}}`/`{{Today}}`/`{{Time}}`，以及 `Muse.txt` 里的五个回呼占位符（`{{delegation_id}}`、`{{source_agent}}`、`{{status}}`、`{{archive_abspath}}`、`{{report}}`），无未注册项。

设计评审与实测共产出两批修正，都已回写 spec：`WeWriteHumanness` 的虚构文体适配见 §7.1，其余 20 条评审发现见 §11。

---

## 实施顺序的理由

依赖方向决定顺序，逆序会返工：

1. **Skill 先于 Agent** —— Agent 定义要引用 Skill 里的 schema 与规则名。
2. **`novel-ledger` 先于其他 Skill** —— 它定义四本账、节拍表、verdict 的字段格式，其余 Skill 与所有 Agent 都引用。
3. **`Muse` 最后** —— 它的事件表要引用其余 5 个 Agent 的确切 `chineseName` 与产物路径。
4. **注册在 Agent 文件写完后** —— 注册未写完的 Agent 会让调用拿到空提示词。

## 本项目的验证方式（无单元测试）

`AGENTS.md` 明确本项目"无正式测试，采用生产验证"。因此每个任务的验证是真实可执行的检查，而不是 `pytest`：

- **Skill 生效验证**：分两层，两层都要过。
  1. *文件层* —— 手动跑 `node Plugin/SkillBridge/SkillBridge.js`，检查 `Plugin/SkillBridge/skill-index.txt` 是否收录该 skill 的 `description`。
  2. *运行层* —— **必须重启服务器。** `Plugin/SkillBridge/plugin-manifest.json` 的 `pluginType` 是 `static` 且**没有 `refreshIntervalCron`**，`Plugin.js` 只在 `initializeStaticPlugins` 时执行一次并把输出缓存在内存里。新建 SKILL 目录后，磁盘上的索引更新了，但运行中的服务器仍然提供启动时的旧值，`{{VCPSkillBridge}}` 里看不到新技能。只做第 1 层验证会得出"已生效"的错误结论。
- **Agent 生效验证**：用一次 `AgentAssistant` 即时通讯（不带 `task_delegation`）单发给该 Agent，看它是否按人格回话。
- **端到端验证**：跑一本 5 章短篇，逐章检查黑板文件是否按 schema 落盘。

### 三类改动的生效方式

| 改动 | 生效方式 |
|---|---|
| `Agent/*.txt` | 即时（`agentManager.js` 有 chokidar 监听 + 缓存失效） |
| `agent_map.json` | 即时（同上） |
| `Plugin/AgentAssistant/config.json` | **必须走 AdminPanel 保存**，触发 `POST /admin_api/agent-assistant/config` → `reloadConfig()`（`routes/admin/agentAssistant.js:29-62`）。无文件监听，手改文件不生效 |
| `SkillBridge/SKILL/**` | **必须重启服务器**（static 插件无 cron） |

服务器当前正在 `node server.js` 运行中（终端 1）。

---

## 文件清单

**新建（10 个）**

| 路径 | 职责 |
|---|---|
| `Plugin/SkillBridge/SKILL/novel-ledger/SKILL.md` | 四本账 / 节拍表 / verdict 的字段规范与刷新纪律 |
| `Plugin/SkillBridge/SKILL/webnovel-craft/SKILL.md` | 网文工艺：爽感、钩子、节奏、视角 |
| `Plugin/SkillBridge/SKILL/story-architecture/SKILL.md` | 弧型模板、角色弧光、伏笔、指南针 |
| `Plugin/SkillBridge/SKILL/story-architecture/references/genre-xianxia.md` | 仙侠题材 preset |
| `Plugin/SkillBridge/SKILL/manuscript-review/SKILL.md` | 六维评分 schema、举证要求、WeWriteHumanness 用法 |
| `Agent/Atlas.txt` | 架构师 |
| `Agent/Inkwell.txt` | 执笔 |
| `Agent/Umpire.txt` | 审稿 |
| `Agent/Chisel.txt` | 修订 |
| `Agent/Ledger.txt` | 记账 |
| `Agent/Muse.txt` | 总编（编排枢纽） |

**修改（2 个）**

| 路径 | 改动 |
|---|---|
| `agent_map.json` | 追加 6 个别名映射 |
| `Plugin/AgentAssistant/config.json` | `agents[]` 追加 6 个对象（只注册中文名，不加英文别名）；`delegationMaxRounds` 保持 15 不动 |

---

## Task 1: Skill `novel-ledger`

这是地基。其余所有文件都引用它定义的字段格式。

**Files:**
- Create: `Plugin/SkillBridge/SKILL/novel-ledger/SKILL.md`

- [ ] **Step 1: 写 SKILL.md**

frontmatter 必须包含 `name`、`description`（含 Triggers 关键词）、`license`、`metadata`。格式参照 `Plugin/SkillBridge/SKILL/pptx-generator/SKILL.md` 第 1-11 行。

正文必须包含的小节，内容从 spec §5.2–5.6 逐字搬运（不要重新发明字段名）：

1. **黑板目录结构** —— spec §5.1 的完整目录树
2. **单写入者原则 + 写入者对照表** —— spec §5.6。这是最容易被违反且后果最严重的规则，放在最前面
3. **`EditFile` vs `WriteFile` 纪律** —— `WriteFile` 同名会自动改名成 `status_card(1).md` 导致台账分裂；刷已存在的账必须用 `EditFile`；首次创建才用 `WriteFile`
4. **四本账字段规范** —— `status_card.md` / `hooks.md` / `timeline.md` / `cognition.md` 的完整 markdown 模板
5. **`progress.md` 与 `run.md`** —— 两者的字段与各自的唯一写入者
6. **伏笔债务算法** —— `债务 = 当前章号 − 最近推进章号`；>20 时审稿标注，>40 时总编提醒用户
7. **节拍表模板** —— spec §5.3
8. **verdict 模板与判定规则** —— spec §5.4，含"任一维度 ≤4 分或存在阻塞级待修项 → FAIL"
9. **`log/debt.md` 带病上线格式** —— spec §5.5

- [ ] **Step 2: 验证 SkillBridge 收录**

SkillBridge 是 `static` 插件，启动时扫描。触发重扫后检查索引：

```powershell
Select-String -Path Plugin/SkillBridge/skill-index.txt -Pattern "novel-ledger"
```

Expected: 命中一行，含该 skill 的 description 摘要。若 `skill-index.txt` 不存在或未命中，检查 frontmatter 的 YAML 是否合法（`description` 含冒号时必须加引号）。

- [ ] **Step 3: Commit**

```bash
git add Plugin/SkillBridge/SKILL/novel-ledger/SKILL.md
git commit -m "feat(novel): add novel-ledger skill defining blackboard schemas"
```

---

## Task 2: Skill `webnovel-craft`

**Files:**
- Create: `Plugin/SkillBridge/SKILL/webnovel-craft/SKILL.md`

- [ ] **Step 1: 写 SKILL.md**

`description` 的 Triggers 关键词至少含：网文、小说、章节、爽点、钩子、写作。

正文小节：

1. **爽感密度** —— 爽点类型（打脸 / 反转 / 实力揭示 / 情感回报 / 信息差揭穿），单章建议 1 个主爽点，投放位置在全章 60–80% 处
2. **章末钩子类型库** —— 悬念 / 危机 / 信息揭示 / 情感悬置 / 反转预告，每类给 2-3 个具体写法与一句示例。**连续章节不得使用同类型钩子**（防结构雷同）
3. **节奏张弛** —— 章节类型交替规则（爆发章后接铺垫章）、单章内的张弛曲线
4. **信息投放速度** —— 设定不得一次倾倒，按需披露；每章新增设定量的上限
5. **视角控制** —— 深度第三人称限知视角为默认；视角不得在同一场景内跳跃；写作时必须对照 `state/cognition.md` 确认该视角角色知道什么
6. **对话与心理描写** —— 对话要有区分度（不同角色说话方式必须不同）；禁止用心理描写代替行动
7. **反面清单** —— 中文网文特有雷点：滥用"某种程度上"类虚词、所有人物同一说话方式、空泛抒情、设定倾倒、爽点前置导致后继无力

- [ ] **Step 2: 验证收录**

```powershell
Select-String -Path Plugin/SkillBridge/skill-index.txt -Pattern "webnovel-craft"
```

Expected: 命中。

- [ ] **Step 3: Commit**

```bash
git add Plugin/SkillBridge/SKILL/webnovel-craft/SKILL.md
git commit -m "feat(novel): add webnovel-craft skill"
```

---

## Task 3: Skill `story-architecture` + 仙侠 preset

**Files:**
- Create: `Plugin/SkillBridge/SKILL/story-architecture/SKILL.md`
- Create: `Plugin/SkillBridge/SKILL/story-architecture/references/genre-xianxia.md`

- [ ] **Step 1: 写 SKILL.md**

正文小节：

1. **指南针（Compass）写法** —— `compass.md` 的三个字段：终局方向、活跃长线、规模估计。每次卷边界由 `Atlas` 更新，故事方向允许随创作演化
2. **分层大纲与滚动展开** —— `outline.md` 只详细展开当前卷；后续卷是骨架弧（仅 goal + 预估章数），写到时再展开。初始规划生成 2 卷起步
3. **五种弧型模板** —— 成长突破 / 竞技对抗 / 探索发现 / 恩怨冲突 / 日常过渡。每种给：典型章数、节拍密度、适用题材、常见失败模式
4. **角色弧光** —— 主角的欲望 / 缺陷 / 转变节点；配角的功能定位
5. **伏笔埋设与回收** —— 埋设时必须同时登记预计回收章；回收前须有至少 1 次推进；三类伏笔（近期 5 章内 / 中期 20 章内 / 长线全书）的配比建议
6. **展开下一弧的输入清单** —— 上一弧摘要 + 角色档案 + 状态卡 + 指南针，明确"不读历史正文"

- [ ] **Step 2: 写 `references/genre-xianxia.md`**

题材 preset 的内容结构（新建一本书时拷进该书的 `style.md`，拷完与 preset 解耦）：

- 世界观骨架：境界体系、修炼资源、势力类型
- 时代事实包：该题材约定俗成的设定常识
- 特有写作风格：语体（半文半白的程度）、称谓体系
- **禁用风格黑名单**：该题材里显得外行或出戏的写法
- 特有铁律：境界不可跳跃、资源必须有来源等自洽约束

- [ ] **Step 3: 验证收录**

```powershell
Select-String -Path Plugin/SkillBridge/skill-index.txt -Pattern "story-architecture"
```

Expected: 命中。`references/` 下的文件不进索引，由 Agent 按需读取，这是正常的渐进披露行为。

- [ ] **Step 4: Commit**

```bash
git add Plugin/SkillBridge/SKILL/story-architecture/
git commit -m "feat(novel): add story-architecture skill with xianxia genre preset"
```

---

## Task 4: Skill `manuscript-review`

**Files:**
- Create: `Plugin/SkillBridge/SKILL/manuscript-review/SKILL.md`

- [ ] **Step 1: 确认 `WeWriteHumanness` 的真实调用契约**

写 Skill 前先核对参数名，不要凭记忆写：

```powershell
Select-String -Path Plugin/WeWriteHumanness/plugin-manifest.json -Pattern "commandIdentifier|content|file_path|mode|tier3_score"
```

记录：命令名 `Score`；参数 `content` 或 `file_path` 二选一；可选 `mode`（`summary` / `json`）、`tier3_score`（0-1 浮点，Agent 自评的语义层分数）。

- [ ] **Step 2: 写 SKILL.md**

正文小节：

1. **审稿的立场** —— 对抗式，默认拒稿。职责是找出问题，不是肯定作者。**每一项扣分都必须引用原文作为证据**，无证据的扣分无效
2. **两段式流程** —— 先跑 `WeWriteHumanness`（零 LLM 成本拿客观分与最弱 5 维），再做六维 LLM 评审并回传自评 `tier3_score`
3. **`WeWriteHumanness` 调用格式** —— 完整 TOOL_REQUEST 示例，用 `file_path` 指向 `chapters/chNNN.md`，`mode` 用 `summary`
4. **11 维适用性表** —— 实测证明 5 个维度（`real_sources` / `word_temperature_mix` / `self_correction` / `negative_emotion_ratio` / `broken_sentences`）对虚构文体结构性不适用且恒定得 0 分，会把综合分严重拖低。**综合分不可作判据**
5. **文体校正算法** —— 只取 6 个适用维度自算 `(1 − 六项均值) × 100`；用 `mode: json` 而非 `summary`；不传 `tier3_score`
6. **六维评分表** —— 爽感密度 / 设定自洽 / 节奏张力 / 人设一致 / 叙事衔接 / 追读引力，每维 0-10，给出 4 分（不合格）、7 分（合格）、9 分（优秀）的锚点描述
7. **节拍表履约核对** —— 逐条对照 `plans/chNNN.plan.md`，未落实的条目必须列出
8. **严重度分级** —— 阻塞 / 高 / 中 / 低的判定标准
9. **判定规则** —— 任一维度 ≤4 或存在阻塞级 → FAIL
10. **骨架检测器** —— 若正文里出现本 Skill 示例中的占位符（如 `<一句话>`、`…`），说明模型照抄了模板，直接判 FAIL
11. **待修项交付格式** —— 最多 3 条给 `Chisel`，按严重度排序，每条含问题 + 原文位置 + 具体改法

- [ ] **Step 3: 验证收录**

```powershell
Select-String -Path Plugin/SkillBridge/skill-index.txt -Pattern "manuscript-review"
```

Expected: 命中。

- [ ] **Step 4: Commit**

```bash
git add Plugin/SkillBridge/SKILL/manuscript-review/SKILL.md
git commit -m "feat(novel): add manuscript-review skill"
```

---

## Task 4b: 解开冷启动死结

**这是设计缺陷的修补，不是新功能。** 原设计有一个第 1 章跑不起来的死结：

- `state/` 下 5 本账的唯一写入者是 `Ledger`，而 `Ledger` 是每章的**最后**一棒
- 但 `Inkwell` 写第 1 章时就要**读**这些文件
- `EditFile` 遇到文件不存在会报错，所以 `Ledger` 首棒也刷不了账
- 建基工序只产出 6 份设定文件，不含 `state/`

结果：ch001 的 `Inkwell` 所有 `ReadFile` 全部 ENOENT。

同一处还有第二个空档：`Inkwell` 要读「最近 3 章摘要」，而 ch001–ch003 时 `summaries/` 里对应文件根本不存在。

**修法（两处，都要做）：**

- [ ] **Step 1: 在 `novel-ledger` 技能里加「冷启动 bootstrap」小节**

内容：建目录清单 + 6 份空台账模板（`status_card.md` / `hooks.md` / `timeline.md` / `cognition.md` / `progress.md` / `run.md`）的初始形态，明确"首次创建用 `WriteFile`，此后一律 `EditFile`"。空台账要有完整表头，这样 `Inkwell` 读到的是一份合法的空表而不是报错。

- [ ] **Step 2: 把 bootstrap 挂到建基工序上**

由 `Atlas` 在建基那一棒顺带完成（它本来就要 `CreateDirectory`），比让 `Muse` 单独多派一棒省一次委派。写进 `Agent/Atlas.txt` 的建基模式，并在 `Muse.txt` 的 `ATLAS_FOUNDATION_COMPLETE` 事件里把验证范围从 6 份设定文件扩展到 6 份设定 + 6 份空台账 + 7 个子目录。

- [ ] **Step 3: 允许摘要文件缺失**

在 `Inkwell` 的委派模板里明确写：「列出的最近 3 章摘要文件若不存在则跳过，**不视为失败**」。否则 ch001–ch003 会被 `Inkwell` 自己判成任务失败。

---

## Task 5: 五个工种 Agent 定义

`Atlas` / `Inkwell` / `Umpire` / `Chisel` / `Ledger`。它们都是**被委派方**，不是主控。

**Files:**
- Create: `Agent/Atlas.txt`、`Agent/Inkwell.txt`、`Agent/Umpire.txt`、`Agent/Chisel.txt`、`Agent/Ledger.txt`

- [ ] **Step 1: 先读两个范本，照格式写**

```powershell
Get-Content Agent/DeepResearcher.txt -TotalCount 20
Get-Content Agent/MemoMaster.txt -TotalCount 30
```

`DeepResearcher.txt`（即 `Sage`）是被委派方的范本，`MemoMaster.txt` 是工种型 Agent 的范本。共同结构：顶部记忆/RAG 注入区 → 角色定义 → 工作模式 → 执行流程 → 安全机制 → 输出规范 → 工具箱占位符。

- [ ] **Step 2: 每个 Agent 文件的必备内容**

五个文件共同遵守：

- 顶部注入区：`[[<代号>日记本::Time::Group::TagMemo+]]` + 公共日记本
- 声明"我几乎总是在异步委托模式下被唤起"
- **不要重复** `delegationSystemPrompt` 已全局注入的内容（禁止伪造工具结果、验证容错、谨慎判定失败、`ServerFileOperator` 铁律）。只写本职工作
- 明确"我只读这些文件"清单（spec §4.2 的隔离边界）
- 明确"我只写这些文件"（spec §5.6 的单写入者表）
- 落盘后必须 `FileInfo` 验证，`[[TaskComplete]]` 回执必须附验证通过的绝对路径
- 工具箱：`{{VCPFileToolBox}}` + `{{VCPSkillBridge}}`
- 时间锚点：`今天是 {{Date}} {{Today}}, 现在是 {{Time}}。`

各自的特殊要求：

**`Atlas.txt`** —— 两种模式：`建基`（首次，产出 6 份设定文件）与 `展弧`（弧末，写弧摘要 + 展开下一弧 + 更新指南针）。必须按需读 `story-architecture` 技能。展弧时明确不读历史正文，只读弧摘要 + 角色档案 + 状态卡 + 指南针。

**`Inkwell.txt`** —— 一棒内两段产出：先 `plans/chNNN.plan.md`（节拍表），再 `chapters/chNNN.md`（正文）。**顺序不可颠倒**，节拍表是审稿核对履约的依据。必读文件清单见 spec §6.3。必须按需读 `webnovel-craft` 与 `novel-ledger`。写作前须对照 `state/cognition.md` 确认视角角色的认知边界。

**`Umpire.txt`** —— 两段式：先调 `WeWriteHumanness`，再六维评审。**默认拒稿立场**要写死。每项扣分必须引用原文。产出 `reviews/chNNN.verdict.md`。必须按需读 `manuscript-review`。回执里明确报 `判定: PASS` 或 `判定: FAIL`，这是 `Muse` 分诊的依据。

**`Chisel.txt`** —— 只按 verdict 的 top-3 待修项精修，**不重写全文**。用 `ApplyDiff` 做局部替换优于整章 `EditFile`（省 token 且不破坏未被指出的部分）。改完后不自评通过与否，由 `Umpire` 复审。

**`Ledger.txt`** —— **只读终稿正文 + 上一版台账**，明确写"我不读节拍表、不读评审判决"，并写明原因（防立场后门）。一棒刷完 `status_card.md` / `hooks.md` / `timeline.md` / `cognition.md` / `progress.md` + 写 `summaries/chNNN.md`。全部用 `EditFile`（首次创建除外）。

- [ ] **Step 3: 逐个验证 Agent 能被调用**

先做 Task 6 的注册，再回来验证。注册后用即时通讯单发测试（不带 `task_delegation`），例如对 `Ledger`：

```
<<<[TOOL_REQUEST]>>>
tool_name:「始」AgentAssistant「末」,
agent_name:「始」记账「末」,
prompt:「始」我是测试者。请用一句话说明你的职责，以及你明确不读哪两类文件。「末」
<<<[END_TOOL_REQUEST]>>>
```

Expected: 回答里提到"不读节拍表、不读评审判决"。若回答泛泛而谈或自称通用助手，说明 `systemPrompt` 的 `{{Ledger}}` 占位符没解析到文件——检查 `agent_map.json` 的别名拼写。

- [ ] **Step 4: Commit**

```bash
git add Agent/Atlas.txt Agent/Inkwell.txt Agent/Umpire.txt Agent/Chisel.txt Agent/Ledger.txt
git commit -m "feat(novel): add five worker agent definitions"
```

---

## Task 6: 注册 6 个 Agent

**Files:**
- Modify: `agent_map.json`
- Modify: `Plugin/AgentAssistant/config.json`

- [ ] **Step 1: 追加 `agent_map.json` 映射**

在现有 JSON 对象内追加（注意上一行要补逗号）：

```json
"Muse": "Muse.txt",
"Atlas": "Atlas.txt",
"Inkwell": "Inkwell.txt",
"Umpire": "Umpire.txt",
"Chisel": "Chisel.txt",
"Ledger": "Ledger.txt"
```

- [ ] **Step 2: 验证热重载生效**

`modules/agentManager.js` 有文件监听。改完看服务器日志（终端 1）：

Expected: 出现 `[AgentManager] Detected change in agent_map.json. Reloading agent map...` 与 `Loaded N agent mappings`，N 比之前多 6。

- [ ] **Step 3: 通过 AdminPanel 添加 6 个 Agent**

`config.json` **没有文件监听器**，热重载只能靠 AdminPanel 保存时调用 `reloadConfig()`（`routes/admin/agentAssistant.js:54-56`）。所以走界面添加，不要手改文件。

每个 Agent 的字段（`temperature` 取自 spec §4.2，`modelId` 取自 §4.4）：

| chineseName | baseName | systemPrompt | temperature | modelId | 计费 |
|---|---|---|---|---|---|
| 缪斯 | MUSE | `{{Muse}}` | 0.3 | `gemini-3.6-flash` | 免费 |
| 架构师 | ATLAS | `{{Atlas}}` | 0.6 | `deepseek-v4-pro` | 付费 |
| 执笔 | INKWELL | `{{Inkwell}}` | 0.85 | `deepseek-v4-pro` | 付费 |
| 审稿 | UMPIRE | `{{Umpire}}` | **0.01** | `deepseek-v4-pro` | 付费 |
| 修订 | CHISEL | `{{Chisel}}` | 0.5 | `deepseek-v4-pro` | 付费 |
| 记账 | LEDGER | `{{Ledger}}` | 0.2 | `gemini-3.6-flash` | 免费 |

模型 ID 已核对上游 `/v1/models` 并逐个实测调通，逐字照抄（注意是 `gemini-3.6-flash`，中间有连字符）。分配依据见 spec §4.4，可用性实测见 §4.4.1。

⚠️ **不要用 `qwen3.6:latest`。** 实测 HTTP 500——本地 Ollama 没装这个模型（只有 `llama3.2:3b` / `bge-m3` / `llama3`）。顺带一提：现有 `HELM` / `HELM_CN` / `QUILL` 三个 Agent 就配在这个 ID 上，现在应该也是坏的，建议一并处理。

⚠️ **`架构师` 原定 `gemini-3.6-flash`，冒烟测试后升为 `deepseek-v4-pro`。** 该档会伪造 `FileInfo` 验证结果（漏写文件却报告已验证通过），详见 spec §4.4.2。`缪斯` 与 `记账` 仍在免费档，需在试跑中确认是否有同样倾向。

⚠️ **审稿的 temperature 必须写 `0.01` 而不是 `0`。**

```200:201:Plugin/AgentAssistant/AgentAssistant.js
                maxOutputTokens: parseInt(maxOutputTokens || '40000', 10),
                temperature: parseFloat(temperature || '0.7'),
```

`0 || '0.7'` 在 JS 里等于 `'0.7'`——填 0 会被**静默**改成 0.7。审稿的确定性是整个质量门禁的命门，跑在 0.7 上意味着同一份稿子两次评审可能一次 PASS 一次 FAIL。`maxOutputTokens` 有同样的陷阱，但我们不填 0，不受影响。

**只注册这 6 个，不要注册英文别名。**

原本打算给每个 Agent 再注册一个 `_EN` 后缀的英文别名对象，让 `callback_agent` 可以用英文名。这个做法是错的：

```953:954:Plugin/AgentAssistant/AgentAssistant.js
    const userSessionId = `agent_${agentConfig.baseName}_delegation_session`;
    const lockKey = `${agentConfig.baseName}::${userSessionId}`;
```

委派的**占线锁和会话历史都由 `baseName` 派生**。注册 `MUSE` 和 `MUSE_EN` 两个 baseName，等于同一个 Agent 有两把独立的锁和两份互不相干的对话历史，"同一 Agent 不能同时派两个活"这条保护就失效了。

正确做法：`agents[]` 以 `chineseName` 为键（`AgentAssistant.js:196`），回呼目标查的是同一张表（`delegationCallbacks.js:166-172`，**查不到直接 `throw`，不降级为无回调**），所以中文名本身就能解析。委派模板里一律写 `callback_agent:「始」缪斯「末」`。本设计是中心辐射拓扑，只有总编做回呼目标，英文别名一个都不需要。

`maxOutputTokens` 统一 60000（与现有 Agent 一致）。`description` 写一句职责。

- [ ] **Step 4: 不要改 `delegationMaxRounds`，保持 15**

原计划要把它从 15 提到 30，理由是"`Inkwell` 一棒内要读 6 份写 2 份、工具调用轮次会超过 15"。这个理由把两个不同的计数搞混了：

- 一个 delegation「轮次」= 一次完整的 `/v1/chat/completions` 调用（`AgentAssistant.js:1006` 的 `while (state.currentRound < DELEGATION_MAX_ROUNDS)`）
- 单次 chat/completions **内部**的工具执行由主服务器的 VCP 循环负责，上限是 `config.env` 的 `MaxVCPLoopNonStream=5`

所以 15 轮 × 每轮最多 5 次工具迭代 = 75 次工具调用机会，`Inkwell` 的 8 次文件操作绰绰有余。

而且 `delegationMaxRounds` 位于 `config.json` 顶层，是**全局**设置——15→30 会把现有 21 个 Agent（含整个交易团队）的失控成本上限一起翻倍。

真正会先撞上的是 `delegationTimeout = 1200000`（20 分钟）：写 3000 字正文 + 8 次文件操作，慢模型下有超时风险。**先不动任何配置，用第 1 章的实测数据决定要不要调，要调就调 timeout 而不是 rounds。**

- [ ] **Step 5: 验证配置已加载**

保存后看服务器日志。

Expected: `[AgentAssistant Service] Config reloaded: N agents loaded.`，N 比之前多 12。

- [ ] **Step 6: 回到 Task 5 Step 3 逐个验证 5 个工种 Agent**

- [ ] **Step 7: Commit**

```bash
git add agent_map.json
git commit -m "feat(novel): register six novel-team agent aliases"
```

`Plugin/AgentAssistant/config.json` 被该目录 `.gitignore` 忽略，不进版本控制，无需 commit。

---

## Task 7: `Muse.txt` 总编

最后写，因为它要引用其余 5 个 Agent 的确切调用名与产物路径。

**Files:**
- Create: `Agent/Muse.txt`
- Modify: `agent_map.json`（Task 6 已含 `Muse` 映射，此处无需再改）

- [ ] **Step 1: 以 `Agent/Helm.txt` 为骨架改写**

```powershell
Get-Content Agent/Helm.txt | Measure-Object -Line
```

`Helm.txt` 是已跑通的同构主控，**直接照它的结构改写**，不要另创一套。必须原样继承的小节：

- **工具调用输出门禁**（`Helm.txt:44-46`）—— 本轮要调工具时必须输出裸露的 `<<<[TOOL_REQUEST]>>>` 块，禁止用 HTML/Markdown 状态卡片包裹，包裹后工具块失效。总编既要编排又要推进度，最容易踩这条。
- **严禁手写或猜测 `{{VCP_ASYNC_RESULT::...}}` 占位符**（`Helm.txt:50`）—— 只能原样复制工具返回的内容，不得自造 `delegationId`。
- **分清两类路径**（`Helm.txt:101`）—— 回呼里的 `{{archive_abspath}}` 是 AgentAssistant 的**自动存档，不是产物正文**；真正的章节/台账路径要从回执正文 `{{report}}` 里读，再用 `ServerFileOperator` 做一次独立 `FileInfo` 验证。把存档路径当产物路径传给下一棒是已知踩坑点。
- 异步委派铁律五条（尤其"一次只发起一个委派"与"异步不轮询"）
- `ServerFileOperator` 调用铁律（命令与参数名对照表、严禁 `FileOperator`、文件名不靠猜、ENOENT 排查顺序、连续失败 2 次停手）
- 状态门禁（`callback_on: always` 下先读 `状态:` 字段再推进）
- **`TopicSponsor` 失败兜底**（`Helm.txt:141`）—— `TopicSponsor` **不在本仓库**，它是 VCPChat 桌面端提供的分布式插件，桌面客户端未连接时调用会失败。必须写明"返回错误就把 `topic_id` 记为无，继续下一步，不要卡在话题创建"。进度可见性是加分项，不是流水线的前置条件。

- [ ] **Step 2: 写 `Muse` 特有的编排内容**

- **花名册表** —— 6 行，含 `agent_name`（中文调用名）、职责、我给的输入、它给我的产出
- **事件表** —— spec §6.2 的 7 行，含每个 `MUSE_EVENT:*` 对应的动作与"是否等用户"。原表有四个分支缺失，必须补上：
  1. **弧末入口** —— `LEDGER_BOOKKEEP_COMPLETE` 那一行只写了"汇报本章完成 → 等验收"，没说下一步派谁。补：若 `章号 % 5 == 0` 则改派 `架构师` 展开下一弧，否则派 `执笔` 写下一章。
  2. **`待用户验收` 字段要参与判断** —— `run.md` 有这个字段但恢复表只看 `在途工序`。`在途工序=无` 且 `待用户验收=ch012` 时，按原表总编会直接开写 ch013，绕过验收检查点。
  3. **三个检查点之后的续跑路径** —— 用户说"继续"时总编走哪条分支必须定义，否则它会自己发明。
  4. **`本章修订轮次` 在失败路径上也要 +1** —— 原设计只在 `CHISEL_REVISE_COMPLETE` 成功时递增。修订反复崩溃时走失败分支，计数永不前进，"两轮封顶绝不无限重试"在崩溃路径上不成立。
- **每章循环的 4 道工序** —— spec §6.1，含审稿 FAIL 时的分诊（轮次 <2 派 `Chisel`；=2 记 `debt.md` 后放行）
- **三类人工检查点** —— 设定确认 / 每章验收 / 弧末方向确认
- **`state/run.md` 写入纪律** —— 每次发起委派**之前**先用 `EditFile` 把 `在途工序` 更新为即将开始的工序。这是崩溃恢复的唯一依据
- **崩溃恢复表** —— spec §6.4 的 5 行
- **委派模板 —— 必须逐字写全 6 个，不能只给 1 个再说"其余同理"**

`Helm.txt` 能跑通，正是因为它把 5 个模板逐字硬编码了。原因是**没有任何占位符提供 `callback_*` 参数的文档**：`toolbox_map.json` 只有 5 个收纳箱（`VCPFileToolBox` / `VCPMemoToolBox` / `VCPMediaToolBox` / `VCPSearchToolBox` / `VCPContactToolBox`），其中 `{{VCPContactToolBox}}` 对 AgentAssistant 只列了 `agent_name` / `prompt` / `temporary_contact` / `timely_contact` / `task_delegation` / `query_delegation`，**完全没有 `inject_tools` 和五个 `callback_*`**。所以总编若要靠占位符现学委派语法，是学不到的——模板必须写死在提示词里。

（`{{VCPCommunicationToolBox}}` 这个占位符**根本不存在**于 `toolbox_map.json`，不要用。`TVStxt/CommunicationToolbox.txt` 是个孤儿文件，全仓无任何注册引用，且内容只有 TopicSponsor。`Helm.txt:363` 写的也是这个死占位符，属于既有 bug，不要照抄。）

6 个模板及各自的 `inject_tools`：

| 模板 | `agent_name` | `inject_tools` |
|---|---|---|
| 建基（含 bootstrap） | `架构师` | `ServerFileOperator` |
| 弧末展开 | `架构师` | `ServerFileOperator` |
| 写章 | `执笔` | `ServerFileOperator` |
| 审稿 | `审稿` | `ServerFileOperator,WeWriteHumanness` |
| 修订 | `修订` | `ServerFileOperator` |
| 记账 | `记账` | `ServerFileOperator` |

审稿那一棒**必须**注入 `WeWriteHumanness`，否则它跑不了客观评分。首轮委派若要开进度话题，再加 `TopicSponsor`。

每个模板一律 `callback_agent:「始」缪斯「末」`（用中文名，理由见 Task 6），`callback_on:「始」always「末」`，`callback_inject_tools` 至少含 `AgentAssistant,ServerFileOperator`。

每个模板的 prompt 都要带 **「自动交接」告知句**（`Helm.txt:169/201/244/331`）：「本次委派已由 AgentAssistant 配置 callback_agent，你完成后不要再调用 AgentAssistant 回呼总编，避免重复交接。」少这一句，下游会自己再回呼一次造成重复交接。

写章模板还要额外写明：「列出的最近 3 章摘要文件若不存在则跳过，不视为失败」（见 Task 4b Step 3）。
- **`Muse` 自己的工具** —— `{{VCPContactToolBox}}`（通讯类，含 AgentAssistant 与 TopicSponsor 的基础说明）、`{{VCPFileToolBox}}`（`FileInfo` 二次验证 + 写 `run.md`）。注意这两个占位符都**不含** `callback_*` 参数文档，所以委派模板必须硬编码
- **触发关键词** —— "写一本小说""开一本新书""继续写第 N 章"等

- [ ] **Step 3: 验证 `Muse` 能被调用且认得团队**

```
<<<[TOOL_REQUEST]>>>
tool_name:「始」AgentAssistant「末」,
agent_name:「始」缪斯「末」,
prompt:「始」我是测试者。请列出你团队的 6 个成员及各自职责，并说明每章有几道工序。不要调用任何工具。「末」
<<<[END_TOOL_REQUEST]>>>
```

Expected: 准确列出 6 个成员，说明每章 3 道常规工序 + 审稿不过时插 1 棒修订。

- [ ] **Step 4: Commit**

```bash
git add Agent/Muse.txt
git commit -m "feat(novel): add Muse orchestrator agent"
```

---

## Task 8: 端到端验证（5 章短篇）

这是唯一能证明系统真的可用的步骤。**不要跳过，也不要一上来就跑 30 章。**

- [ ] **Step 1: 建黑板骨架**

选一个 `book-id`（kebab-case ASCII，避免中文路径编码问题），例如 `test-xianxia-5ch`。让 `Muse` 建目录，或手工建：

```powershell
$b = "E:\VCPChat\VCPToolBox\file\novel\test-xianxia-5ch"
New-Item -ItemType Directory -Force -Path "$b\state","$b\plans","$b\chapters","$b\reviews","$b\summaries","$b\arcs","$b\log" | Out-Null
Get-ChildItem $b -Recurse -Directory | Select-Object FullName
```

Expected: 7 个子目录。

- [ ] **Step 2: 跑设定阶段**

对 `缪斯` 说："开一本新书，book-id 是 test-xianxia-5ch，仙侠题材，5 章短篇，主角是个被逐出宗门的少年。"

Expected：`Muse` 开进度话题 → 委派 `Atlas` → 松手（回复里带 `{{VCP_ASYNC_RESULT::AgentAssistant::<id>}}` 占位符）。

**检查点**：`Muse` 是否真的输出了裸露的 TOOL_REQUEST 块，而不是用 HTML/文字假装已委派。若它只是口头说"已委派"，说明输出门禁那节没写到位。

- [ ] **Step 3: 验证 Atlas 产物**

```powershell
Get-ChildItem "E:\VCPChat\VCPToolBox\file\novel\test-xianxia-5ch" -File | Select-Object Name,Length
```

Expected: `book.md`、`compass.md`、`outline.md`、`characters.md`、`world.md`、`style.md` 六份，`Length` 均 > 0。

- [ ] **Step 4: 跑第 1 章的完整 3 棒**

确认设定后让 `Muse` 推进。逐棒检查：

| 棒 | 产物 | 检查 |
|---|---|---|
| 执笔 | `plans/ch001.plan.md` + `chapters/ch001.md` | 节拍表是否先落盘；正文字数是否接近目标 |
| 审稿 | `reviews/ch001.verdict.md` | 是否含 `WeWriteHumanness` 的客观分；每项扣分是否引用了原文 |
| 记账 | `state/*.md` + `summaries/ch001.md` | 四本账是否都刷新 |

- [ ] **Step 5: 关键回归检查 —— 台账有没有被写成带序号的文件**

这是 spec §9 列的最高危风险。

```powershell
Get-ChildItem "E:\VCPChat\VCPToolBox\file\novel\test-xianxia-5ch\state" -File | Select-Object Name
```

Expected: 恰好 7 个文件（`status_card.md`、`hooks.md`、`hooks_closed.md`、`timeline.md`、`cognition.md`、`progress.md`、`run.md`），**没有任何 `xxx(1).md`**。若出现带序号的，说明某个 Agent 在该用 `EditFile` 的地方用了 `WriteFile`——回去改它的定义文件。

同时检查 `timeline.md` 是否在增长而**没有**被重打：连续两章后行数应该只增不减。若某章之后行数变少，说明记账用了 `EditFile` 而非 `AppendFile`，历史已经丢了。

- [ ] **Step 6: 验证串行铁律没被违反**

翻服务器日志（终端 1），确认同一时刻只有一个委托在跑。

Expected: 日志里 `executeDelegation` 的开始与结束成对出现，不存在两个未结束的委托重叠。若重叠，说明 `Muse` 违反了"一次只发起一个委派"——加强那节措辞。

- [ ] **Step 7: 跑完 2–5 章，观察三件事**

1. **`Umpire` 的拒稿率** —— 若 5 章全 FAIL，判定阈值过严，调整 `manuscript-review` 的评分锚点；若全 PASS，过松，加强"默认拒稿"立场
2. **伏笔债务是否在累积** —— 检查 `state/hooks.md` 的债务列是否随章号增长
3. **`Inkwell` 是否撞轮次上限** —— 若回执报轮次耗尽，把 `delegationMaxRounds` 再往上调

- [ ] **Step 8: 验证崩溃恢复**

第 3 章写作中途手动中断（或等一次真实失败），然后重新对 `缪斯` 说"继续"。

Expected: `Muse` 读 `state/run.md` 的 `在途工序`，`FileInfo` 检查该章产物是否已落盘，据此重派正确的那一棒，而不是从第 1 章重来或跳过。

- [ ] **Step 9: 记录校准结果并 Commit**

把实测得出的阈值调整写回 Skill 文件，并在 spec 的风险表补充实测数据。

```bash
git add Plugin/SkillBridge/SKILL/ docs/superpowers/
git commit -m "fix(novel): calibrate review thresholds from 5-chapter end-to-end run"
```

---

## 完成标准

- [ ] 4 个 Skill 都被 `skill-index.txt` 收录
- [ ] 6 个 Agent 都能被 `AgentAssistant` 按中文名和英文名调用，且回话符合人格
- [ ] 5 章短篇跑通，黑板文件全部符合 schema
- [ ] `state/` 目录没有带序号的重复文件
- [ ] 全程未出现两个并发委托
- [ ] 崩溃恢复验证通过
- [ ] `Umpire` 的拒稿率落在合理区间（既非全过也非全拒）

## 后续（不在本计划范围）

spec §10 的阶段二与阶段三：卷级摘要、`Echo` 读者评审、`NovelLint` 确定性校验插件、`WeWriteLearnEdits` 改稿飞轮、`MagiAgent` 三贤者审稿、`ContextBridge` 语义检索替代固定窗口。
