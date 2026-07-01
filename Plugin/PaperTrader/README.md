# PaperTrader 插件 — 短线模拟盘追踪器

维护一个**最多 N 仓（默认 3）**的模拟盘账本，按《短线操盘实战技法》的买卖点**确定性前向模拟**真实买入/卖出，并统计收益亏损。用于验证 DSA 选股算法 + 书中策略的实际有效性。

## 设计边界

- **纯模拟**：不下单、不接券商、不碰真实资金。
- **确定性**：买卖盈亏全部由代码按规则计算，可复现（不让 LLM 算 P&L）。
- **买卖剧本来自 DSA**：`add_position` 默认从 DSA 的 `factor_snapshot`（`buy_points` / `exit_plan`）自动提取归一化剧本；也可显式传 `plan`。
- **行情只读 DSA**：用 `stock_daily` 日线成交；自有 SQLite 账本读写。

## 模拟规则（已与用户确认）

- 从 `run_trade_date` 向后逐日推进（不在选股当日成交）。
- **买点成交**：仅当 `low ≤ trigger ≤ high`（盘中确实到过该价）才以 `trigger_price` 成交；跳空越过则当日不成交（不追高）。
- **同日止损与止盈同触 → 优先止损**（保守）；跳空低于止损按开盘价成交。
- **最多 MAX_SLOTS 仓**，watching（待触发）也占仓；不强制凑满。
- **分批止盈** 按 `take_profits`；首次止盈后止损上移保本；尾仓 `trailing_final` 用"跌破前一交易日最低价"清仓。
- **时间止损**：watching 超过 `invalidation_days` 个交易日仍未触发首笔买点 → 失效平仓（0 盈亏）。
- **弱市不操作**：由调用方（模拟盘追踪官 Agent）在 `gate=空仓` 时不 `add_position` 来体现。

## 配置

复制 `config.env.example` 为 `config.env`：
```ini
STOCK_DB_PATH=E:/daily_stock_analysis/data/stock_analysis.db   # DSA 行情库(只读)
PAPER_DB_PATH=E:/VCPChat/VCPToolBox/Plugin/PaperTrader/paper_portfolio.db  # 模拟盘账本(自动建)
POSITION_CAPITAL=100000   # 每仓分配资金
MAX_SLOTS=3               # 最多同时追踪仓数
```

## 命令

| command | 说明 | 必需参数 | 可选参数 |
|---------|------|----------|----------|
| `get_portfolio` | 当前持仓 + 浮动盈亏 + 空槽数 | - | - |
| `add_position` | 把一只票加入空槽（默认从 DSA 自动提取剧本，加入后自动模拟到今天） | `code`, `strategy` | `run_id`(自动提取时必需), `name`, `run_trade_date`, `plan` |
| `run_eod_update` | 对所有持仓做 EOD 前向模拟更新 | - | `trade_date`(默认今天) |
| `close_position` | 手动强制平仓 | `code` | `reason` |
| `get_trade_history` | 已平仓交易流水 | - | `limit`(默认100), `strategy` |
| `get_stats` | 绩效统计（胜率/盈亏/分策略/在场浮盈） | - | - |

### 归一化买卖剧本 plan schema（显式传入时）
```json
{
  "entries": [{"trigger_price": 5.10, "ratio": 1.0, "label": "突破P2"}],
  "stop_loss": 3.54,
  "take_profits": [{"gain_pct": 10, "reduce_frac": 0.5}, {"gain_pct": 20, "reduce_frac": 0.25}],
  "trailing_final": true,
  "invalidation_days": 3
}
```

### 自动提取支持度
- `bottom_divergence_double_breakout`：完整（buy_points 分批 + exit_plan 止盈止损）。
- `ma100_low123_combined`：突破 P2 入场 / 低点3(P3) 止损（含文本解析）。
- `shrink_pullback`：entry/stop 字段。
- `trendline_breakout` / `volume_breakout` / `gap_limitup_breakout`：尽力（关键位/缺口位），取不到则需显式传 `plan`。
- `extreme_strength_combo` / `one_yang_three_yin` / `bottom_volume`：无结构化触发价（多为观察/题材），需显式 `plan` 或不入模拟盘。

## 典型调用（VCP 协议，maid 首行）
```
<<<[TOOL_REQUEST]>>>
maid:「始」模拟盘追踪官「末」,
tool_name:「始」PaperTrader「末」,
command:「始」add_position「末」,
run_id:「始」run-xxx「末」,
code:「始」600103「末」,
strategy:「始」ma100_low123_combined「末」
<<<[END_TOOL_REQUEST]>>>
```

## 模块
- `db.py`：DSA 行情只读 + 模拟盘 SQLite 账本(positions/trades_history) CRUD。
- `plan_extractor.py`：从 DSA factor_snapshot 提取归一化买卖剧本。
- `engine.py`：前向逐日模拟引擎 + 盈亏会计 + 统计。
- `paper_trader.py`：stdio 入口与命令路由。

## 已知边界
- 复权口径：买卖剧本与 `stock_daily` 须为同一复权口径（DSA 内部一致即可）。
- 尾仓"跌破最近低点"用"前一交易日最低价"近似。
- 停牌(无 K 线)当日跳过；ST/退市需 `close_position` 手动处理。

## 依赖
纯 Python 标准库（`sqlite3`），无需额外安装。
