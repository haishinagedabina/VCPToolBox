# AgentAssistant 多 Agent 委托交接（Delegation Handoff）设计文档

## 1. 背景与目标

AgentAssistant 支持「异步委托」（`task_delegation:true`）：上游 Agent 把任务交给下游 Agent 后台执行，完成后通过占位符回填结果。

在此之上，本仓库自研了「委托交接（handoff）」能力（`callback_*` 系列参数）：当一个异步委托完成（成功/失败/总是）时，自动把成果再交接给另一个 Agent，形成「调研 → 主编」式的链式协作。

**问题**：原实现把交接逻辑（`buildDelegationCallbackSpec` / `shouldRunDelegationCallback` / `triggerDelegationHandoff` 以及 `executeDelegation` 的签名/调用/finally 块）与上游维护的 `AgentAssistant.js` 物理交织，导致每次从上游 fork 同步代码都在 `processToolCall`、`executeDelegation` 等核心函数上产生冲突。

**目标**：在不改变功能的前提下，把交接逻辑全部下沉到 `delegationCallbacks.js`，让 `AgentAssistant.js` 相对上游仅保留**极少且低风险**的侵入点，未来同步上游基本无冲突。

## 2. 最小侵入原则

- 不改动 `executeDelegation` 的**函数签名**与**调用处**（回退到与上游一致）。
- 不做「逐轮全量转录」（不侵入 `executeDelegation` 的对话循环）。完整性由「最终报告归档 + 下游读取文件」保证。
- 取消、积分、心跳、River、`archiveDelegationReport`、`sendDelegationCallback` 等一律不动。
- 仅做阶段一（不引入 `workflowId` 黑板）。

## 3. 核心手法：旁路注册表（Side-channel Registry）

不再把 `callbackSpec` 作为参数在 `executeDelegation` 系列函数间传递，而是：

- 以 `delegationId` 为 key，把 `{ spec, depth }` 存进 `delegationCallbacks.js` 内部的模块级 `Map`（`pendingCallbacks`）。
- 委托提交时调用 `registerDelegationCallback(delegationId, args, { agents })` 登记。
- 委托收尾时（`executeDelegation` 的 finally）调用 `runDelegationHandoffIfNeeded(delegationId, ctx)` 消费并删除该条目（一次性，防泄漏）。

这样 `executeDelegation` 不再需要 `callbackSpec` 形参，签名与调用处完全回退到上游，消除了最高危的冲突点。

## 4. AgentAssistant.js 的侵入点清单（before / after）

| # | 位置 | Before | After |
|---|------|--------|-------|
| 1 | 顶部 require | `buildDelegationCallbackSpec, renderDelegationCallbackPrompt, shouldRunDelegationCallback` | `registerDelegationCallback, runDelegationHandoffIfNeeded` |
| 2 | `processToolCall` 内 callbackSpec 预解析块 | 约 17 行的解析 + 校验块 | 删除（回退上游原貌） |
| 3 | 委托分支 `delegationId` 生成后 | 无 | 新增 `registerDelegationCallback(...)`（try/catch → `throwToolError`） |
| 4 | `executeDelegation(...)` 调用处 | 末尾带 `, callbackSpec` | 去掉，与上游一致 |
| 5 | `successMessage` | 使用本地 `callbackSpec` | 不变（`callbackSpec` 由侵入点 3 提供） |
| 6 | `executeDelegation` 函数签名 | `..., callbackSpec = null` | 去掉，与上游一致 |
| 7 | `executeDelegation` finally 交接块 | `if (shouldRunDelegationCallback) { triggerDelegationHandoff(...) }` | 单次 `await runDelegationHandoffIfNeeded(delegationId, {...})` |
| 8 | `triggerDelegationHandoff` 函数定义 | 约 29 行 | 删除（已搬入 `delegationCallbacks.js`） |

最终 `AgentAssistant.js` 相对上游只剩：① 改过的 require；② 委托分支内新增的 register 块；③ finally 内一行 `runDelegationHandoffIfNeeded`；④ `successMessage` 的小条件。

## 5. 新参数与新占位符

### 新参数
- `callback_auto_file_tool`（可选，默认 `true`）：交接时是否自动给下游注入 `ServerFileOperator`，以便其读取上游产物文件。
- `callback_prompt` 现在可省略（省略时使用内置结构化交接信封）。

### 新占位符
- `{{report_summary}}`：成果摘要（默认折叠空白并截断至 800 字）。
- `{{archive_abspath}}`：完整报告归档文件的**绝对路径**。

（仍兼容旧占位符：`{{delegation_id}}`、`{{source_agent}}`、`{{status}}`、`{{archive_path}}`、`{{report}}`。）

## 6. 默认结构化交接信封

当未提供 `callback_prompt` 时，使用 `buildDefaultHandoffPrompt` 生成如下信封（占位符由 `renderDelegationCallbackPrompt` 填充）：

```
[自动交接信封] 上游 Agent「{{source_agent}}」的委托任务已结束。
- 委托ID: {{delegation_id}}
- 状态: {{status}}

成果摘要:
{{report_summary}}

完整报告文件（绝对路径）: {{archive_abspath}}
请先使用 ServerFileOperator 的 ReadFile 命令读取上述文件获取完整内容，再开展你的工作。
```

## 7. 可达性保证（完整性如何不丢）

设计决策是「不逐轮转录」，因此下游获取完整上下文依赖三件事：

1. **绝对路径**：信封提供 `{{archive_abspath}}`，避免相对路径在不同工作目录下解析失败。`toAbsPath` 将相对路径基于项目根（`PROJECT_ROOT = Plugin/AgentAssistant 向上两级`）解析。
2. **自动注入 ServerFileOperator**：`autoFileTool` 默认 true，`mergeInjectTools` 在下游 `inject_tools` 中确保包含 `ServerFileOperator`（大小写不敏感去重）。
3. **读取指引**：信封文案明确要求下游先用 `ServerFileOperator` 的 `ReadFile` 读取归档报告再开工。

## 8. 防环（Loop Guard）

- 注册时记录 `depth = parseInt(args.__handoff_depth, 10) || 0`。
- 触发交接时给下游传 `__handoff_depth: depth + 1`。
- `runDelegationHandoffIfNeeded` 在 `depth >= DEFAULT_MAX_DEPTH(5)` 时不再交接，并推送 `type:'warning'` 告警，防止 A→B→A 式无限链。

## 9. 向后兼容

- `buildDelegationCallbackSpec` / `renderDelegationCallbackPrompt` / `shouldRunDelegationCallback` 行为与签名保持兼容，旧测试用例（spec 无 `autoFileTool`、data 仅含 `report`/`archivePath`）仍可渲染。
- 仅放宽了一处规则：允许只给 `callback_agent` 而不给 `callback_prompt`；但给了 `callback_prompt` 却没 `callback_agent` 仍报错。
- 交接失败不外抛异常，不影响主委托收尾。

## 10. 配置默认值

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `handoffSummaryChars`（`DEFAULT_SUMMARY_CHARS`） | 800 | `{{report_summary}}` 截断长度 |
| `handoffMaxDepth`（`DEFAULT_MAX_DEPTH`） | 5 | 交接链最大深度（防环） |
| `callback_auto_file_tool`（`autoFileTool`） | true | 自动注入 `ServerFileOperator` |
| `callback_on` | success | 交接触发条件 |

## 11. 阶段二展望（workflowId 黑板）

后续可引入 `workflowId` 级别的共享「黑板（blackboard）」：

- 为一条完整工作流分配 `workflowId`，沿交接链透传。
- 黑板集中存放各节点产物索引（报告路径、关键摘要、结构化字段），下游按需检索，而非仅靠单个上游归档文件。
- 可支持扇出/汇聚（多下游并行 + 结果聚合）与可观测性（链路可视化）。

本次实现保持阶段一边界，旁路注册表与 `__handoff_depth` 透传机制为阶段二预留了演进空间。
