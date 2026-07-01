# StockScreener 插件

只读访问 [daily_stock_analysis (DSA)](../../../daily_stock_analysis) 的 SQLite 数据库，
为 VCPToolBox 的 Agent 团队提供 **选股结果** 与 **股票行情数据** 的取数能力。

## 设计边界

- **只读**：使用 SQLite `mode=ro` 只读连接，绝不写入 DSA 数据库。
- **只取数**：不触发选股（选股由 DSA 自身定时/手动运行），不做任何 AI 分析。
- **AI 分析归属**：由 VCP Agent 团队消费本插件返回的数据自行分析、追踪、复盘选股算法。

## 配置

复制 `config.env.example` 为 `config.env`，填写 DSA 数据库文件路径：

```ini
STOCK_DB_PATH=E:/daily_stock_analysis/data/stock_analysis.db
STOCK_DB_TIMEOUT=5
```

> DSA 用单文件 SQLite，无账号密码，只需数据库文件的绝对路径。

## 命令

所有命令通过 `command` 参数路由，返回 JSON 结构化数据。

| command | 说明 | 必需参数 | 可选参数 |
|---------|------|----------|----------|
| `list_screening_runs` | 历史选股批次列表（按开始时间倒序） | - | `market`, `status`, `limit`(默认20) |
| `get_screening_candidates` | 某批次候选清单（按 rank 升序） | `run_id` | `limit`(默认100), `with_ai_only` |
| `get_candidate_detail` | 单票完整选股决策详情（五层/因子/选股理由/DSA的AI结论，全量含155字段快照） | `run_id`, `code` | - |
| `get_strategy_evidence` | **单策略域裁剪复核证据包**（仅本策略相关字段+hit_reasons+锚点日期+数据质量+run_trade_date），供选股复核 Agent 防上下文爆炸 | `run_id`, `code`, `strategy` | - |
| `query_stock_daily` | 个股日线行情（追踪走势/核验锚点） | `code` | `start_date`, `end_date`, `limit`(默认60), `columns`(列裁剪), `anchor_dates`(锚点窗口), `window`(默认3) |
| `get_stock_info` | 股票主数据（名称/行业/ST/板块） | `code` | - |

> `strategy` 取值: `bottom_divergence_double_breakout`, `ma100_low123_combined`, `ma100_60min_combined`, `gap_limitup_breakout`, `shrink_pullback`, `trendline_breakout`, `volume_breakout`, `extreme_strength_combo`, `one_yang_three_yin`, `bottom_volume`。

### 调用示例（VCP 工具协议）

```
<<<[TOOL_REQUEST]>>>
tool_name:「始」StockScreener「末」,
command:「始」get_screening_candidates「末」,
run_id:「始」run_20260603_cn_xxx「末」,
with_ai_only:「始」true「末」
<<<[END_TOOL_REQUEST]>>>
```

## 典型 Agent 团队工作流

1. `list_screening_runs` → 找到最近完成的批次 `run_id`
2. `get_screening_candidates(run_id)` → 拿到候选清单
3. `get_candidate_detail(run_id, code)` → 拿到每只票"当初为何被选中 + DSA 的 AI 判断"
4. `query_stock_daily(code, start_date=选股日)` → 拿到选股后的真实走势
5. Agent 对照"选股理由 vs 实际涨跌" → 找出选股算法问题、提出优化建议

## 依赖

纯 Python 标准库（`sqlite3`），无需额外安装。
