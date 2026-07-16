# -*- coding: utf-8 -*-
"""
DSA SQLite 数据库只读访问层。

仅做只读查询，所有 SQL 参数化以防注入。连接使用 SQLite 只读 URI
(``file:...?mode=ro``)，杜绝插件误写 DSA 生产库，并设置 busy_timeout
以规避 DSA 写库期间的锁等待。
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# 选股批次有效完成状态（候选结果可用）
COMPLETED_STATUSES = {"completed", "completed_with_ai_degraded"}

# 候选详情中需要从 JSON 文本列解析成结构化对象并保留的字段。
# 注意：candidate_decision 本身已内嵌 factor_snapshot / rule_hits / matched_strategies，
# 因此不再单独展开那几个 *_json 列（避免成倍的冗余），改为直接丢弃它们。
_CANDIDATE_JSON_KEEP = ("candidate_decision_json", "trade_plan_json")
_CANDIDATE_JSON_DROP = ("matched_strategies_json", "rule_hits_json", "factor_snapshot_json")

# ----------------------------------------------------------------------------
# 策略域证据裁剪（get_strategy_evidence）
# ----------------------------------------------------------------------------
# 复核 Agent 只需"本策略相关"的 factor_snapshot 字段（真实快照 155 字段太大，
# 直接喂会撑爆上下文），因此按策略前缀/字段白名单裁剪，并附该策略的 hit_reasons
# 及从文本解析出的锚点日期。字段命名经真实库实证（prefix 稳定）。

# 所有策略通用的快照字段（行情/趋势/位置背景）
_COMMON_SNAPSHOT_FIELDS = (
    "close", "ma5", "ma10", "ma20", "ma60", "ma100", "above_ma100",
    "volume_ratio", "trend_score", "turnover_rate", "circ_mv",
    "pct_chg", "pct_chg_5d", "pct_chg_20d", "amplitude", "days_since_listed",
    "liquidity_score", "is_st", "breakout_ratio", "close_strength",
)

# 策略 → {prefixes: 前缀白名单, extra: 额外字段, hit_reasons_field: 该策略 reasons 字段,
#         required: 关键字段(缺失则 data_quality=insufficient)}
_STRATEGY_SPECS: Dict[str, Dict[str, Any]] = {
    "bottom_divergence_double_breakout": {
        "prefixes": ("bottom_divergence_",),
        "extra": ("macd_bull_divergence", "macd_bear_divergence"),
        "hit_reasons_field": "bottom_divergence_hit_reasons",
        "required": ("bottom_divergence_state", "bottom_divergence_pattern_code"),
    },
    "ma100_low123_combined": {
        "prefixes": ("ma100_low123_", "pattern_123_"),
        "extra": ("above_ma100", "ma100", "ma100_distance_pct",
                  "ma100_breakout_days", "ma100_bars_since_breakout"),
        "hit_reasons_field": "ma100_low123_hit_reasons",
        "required": ("ma100_low123_confirmed", "ma100_low123_state"),
    },
    "ma100_60min_combined": {
        "prefixes": ("ma100_60min_", "ma100_pre_breakout_"),
        "extra": ("above_ma100", "ma100", "ma100_distance_pct",
                  "ma100_breakout_days", "ma100_bars_since_breakout",
                  "ma100_breakout_bar_index"),
        "hit_reasons_field": "ma100_60min_hit_reasons",
        "required": ("ma100_60min_confirmed",),
    },
    "gap_limitup_breakout": {
        "prefixes": ("breakaway_gap_", "continuation_gap_", "limitup_", "gap_", "retest_"),
        "extra": ("near_recent_rally_peak", "is_limit_up", "limit_up_breakout",
                  "bars_since_breakaway_gap", "bars_since_limitup_structure_breakout",
                  "has_recent_breakaway_event", "above_ma100"),
        "hit_reasons_field": None,
        "required": (),
    },
    "shrink_pullback": {
        "prefixes": ("shrink_pullback_", "pullback_"),
        "extra": ("ma5", "ma10", "ma20", "ma5_distance_pct", "above_ma100"),
        "hit_reasons_field": None,
        "required": (),
    },
    "trendline_breakout": {
        "prefixes": ("trendline_",),
        "extra": ("above_ma100", "volume_ratio", "trend_score", "liquidity_score"),
        "hit_reasons_field": None,
        "required": (),
    },
    "volume_breakout": {
        "prefixes": (),
        "extra": ("breakout_ratio", "volume_ratio", "trend_score",
                  "close_strength", "ma5_distance_pct", "liquidity_score"),
        "hit_reasons_field": None,
        "required": (),
    },
    "extreme_strength_combo": {
        "prefixes": ("extreme_strength_", "leader_score", "theme_",
                     "base_extreme_strength", "base_leader",
                     "effective_extreme", "effective_leader"),
        "extra": ("is_hot_theme_stock", "is_limit_up", "gap_breakaway",
                  "pattern_123_low_trendline", "above_ma100", "turnover_rate", "circ_mv"),
        "hit_reasons_field": None,
        "required": (),
    },
    "one_yang_three_yin": {
        "prefixes": (),
        "extra": ("candle_pattern", "trend_score", "volume_ratio",
                  "breakout_ratio", "liquidity_score"),
        "hit_reasons_field": None,
        "required": (),
    },
    "bottom_volume": {
        "prefixes": (),
        "extra": ("pct_chg_20d", "volume_ratio", "pct_chg",
                  "liquidity_score", "close_strength"),
        "hit_reasons_field": None,
        "required": (),
    },
}

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_missing(value: Any) -> bool:
    """None 或 NaN 视为缺失（用于 data_quality 判定）。"""
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _split_rule_hits_by_strategy(rule_hits: Any) -> Dict[str, List[str]]:
    """把扁平 rule_hits 按 `strategy:<name>` 分隔标记拆成每策略的命中项。"""
    out: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for item in rule_hits or []:
        text = str(item)
        if text.startswith("strategy:"):
            current = text.split(":", 1)[1].strip()
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(text)
    return out


def _extract_anchor_dates(texts: List[Any]) -> List[str]:
    """从 hit_reasons 文本中提取所有 YYYY-MM-DD 锚点日期（去重升序）。"""
    seen: List[str] = []
    for t in texts or []:
        for d in _DATE_RE.findall(str(t)):
            if d not in seen:
                seen.append(d)
    return sorted(seen)


class DbConfigError(Exception):
    """数据库路径配置缺失或文件不存在。"""


def _resolve_db_path() -> str:
    raw = os.environ.get("STOCK_DB_PATH", "").strip()
    if not raw:
        raise DbConfigError(
            "未配置 STOCK_DB_PATH。请在 Plugin/StockScreener/config.env 中设置 DSA 的 "
            "stock_analysis.db 绝对路径。"
        )
    path = Path(raw)
    if not path.exists():
        raise DbConfigError(f"STOCK_DB_PATH 指向的数据库文件不存在: {path}")
    return str(path.absolute())


def _connect() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    timeout = float(os.environ.get("STOCK_DB_TIMEOUT", "5") or 5)
    # 只读 URI 连接：mode=ro 确保无法写入，文件路径需转成 URI 形式
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _safe_json_load(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _to_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 10000) -> int:
    try:
        result = int(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return default
    return max(minimum, min(maximum, result))


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "是"}


# ----------------------------------------------------------------------------
# 查询命令
# ----------------------------------------------------------------------------

def list_screening_runs(
    market: Optional[str] = None,
    status: Optional[str] = None,
    limit: Any = 20,
) -> Dict[str, Any]:
    limit = _to_int(limit, 20, minimum=1, maximum=100)
    where: List[str] = []
    params: List[Any] = []
    if market:
        where.append("market = ?")
        params.append(market.strip())
    if status:
        where.append("status = ?")
        params.append(status.strip())
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT run_id, trade_date, market, status, universe_size, candidate_count, "
        "ai_top_k, error_summary, started_at, completed_at, trigger_type "
        f"FROM screening_runs{clause} ORDER BY started_at DESC LIMIT ?"
    )
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [_row_to_dict(r) for r in rows]
    return {"total": len(items), "runs": items}


def get_screening_candidates(
    run_id: str,
    limit: Any = 100,
    with_ai_only: Any = False,
) -> Dict[str, Any]:
    if not run_id:
        raise ValueError("run_id 为必需参数")
    limit = _to_int(limit, 100, minimum=1, maximum=500)
    ai_only = _to_bool(with_ai_only)

    with _connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, status, trade_date, market, candidate_count FROM screening_runs "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"选股批次不存在: {run_id}")
        run_info = _row_to_dict(run_row)

        where = ["run_id = ?"]
        params: List[Any] = [run_id]
        if ai_only:
            where.append("selected_for_ai = 1")
        sql = (
            "SELECT code, name, rank, rule_score, selected_for_ai, ai_summary, "
            "ai_operation_advice, ai_trade_stage, ai_confidence, trade_stage, setup_type, "
            "entry_maturity, risk_level, market_regime, theme_position, candidate_pool_level "
            "FROM screening_candidates WHERE " + " AND ".join(where) +
            " ORDER BY rank ASC LIMIT ?"
        )
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

    items = [_row_to_dict(r) for r in rows]
    for it in items:
        it["selected_for_ai"] = bool(it.get("selected_for_ai"))
    return {
        "run": run_info,
        "completed": run_info.get("status") in COMPLETED_STATUSES,
        "total": len(items),
        "candidates": items,
    }


def get_candidate_detail(run_id: str, code: str) -> Dict[str, Any]:
    if not run_id:
        raise ValueError("run_id 为必需参数")
    if not code:
        raise ValueError("code 为必需参数")
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM screening_candidates WHERE run_id = ? AND code = ?",
            (run_id, code.strip()),
        ).fetchone()
    if row is None:
        raise ValueError(f"候选不存在: run_id={run_id}, code={code}")
    detail = _row_to_dict(row)
    detail["selected_for_ai"] = bool(detail.get("selected_for_ai"))
    # 丢弃与 candidate_decision 内容重复的冗余 JSON 列
    for field in _CANDIDATE_JSON_DROP:
        detail.pop(field, None)
    # 把保留的 *_json 文本列解析成结构化对象
    for field in _CANDIDATE_JSON_KEEP:
        if field in detail:
            new_key = field[:-5]  # 去掉 "_json" 后缀
            detail[new_key] = _safe_json_load(detail.pop(field))
    return detail


def get_strategy_evidence(run_id: str, code: str, strategy: str) -> Dict[str, Any]:
    """返回某候选在【单个策略域】下裁剪+清洗后的复核证据包。

    专供选股复核 Agent：从 155 字段的 factor_snapshot 中只取本策略相关字段，
    附该策略 hit_reasons 与从文本解析出的锚点日期、数据质量标注、DSA 自带 AI 结论，
    以及 run 的 trade_date（供 Agent 钳制 K线、避免前视偏差）。
    """
    if not run_id:
        raise ValueError("run_id 为必需参数")
    if not code:
        raise ValueError("code 为必需参数")
    if not strategy:
        raise ValueError(
            "strategy 为必需参数。可用: " + ", ".join(sorted(_STRATEGY_SPECS))
        )
    strategy = strategy.strip()
    spec = _STRATEGY_SPECS.get(strategy)
    if spec is None:
        raise ValueError(
            f"未知 strategy: {strategy}。可用: " + ", ".join(sorted(_STRATEGY_SPECS))
        )

    with _connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, trade_date, market, status FROM screening_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"选股批次不存在: {run_id}")
        row = conn.execute(
            "SELECT * FROM screening_candidates WHERE run_id = ? AND code = ?",
            (run_id, code.strip()),
        ).fetchone()
    if row is None:
        raise ValueError(f"候选不存在: run_id={run_id}, code={code}")

    rec = _row_to_dict(row)
    cd = _safe_json_load(rec.get("candidate_decision_json"))
    fs: Dict[str, Any] = {}
    if isinstance(cd, dict) and isinstance(cd.get("factor_snapshot"), dict):
        fs = cd["factor_snapshot"]
    else:
        loaded = _safe_json_load(rec.get("factor_snapshot_json"))
        if isinstance(loaded, dict):
            fs = loaded
    matched = _safe_json_load(rec.get("matched_strategies_json")) or []
    rule_hits = _safe_json_load(rec.get("rule_hits_json")) or []

    prefixes = tuple(spec.get("prefixes", ()))
    extra = set(spec.get("extra", ()))
    strategy_snapshot = {
        k: v for k, v in fs.items()
        if (prefixes and k.startswith(prefixes)) or k in extra
    }
    common_snapshot = {k: fs.get(k) for k in _COMMON_SNAPSHOT_FIELDS if k in fs}

    # hit_reasons：优先策略专属字段（含 _watch_hit_reasons 变体），否则回退按标记拆分 rule_hits
    hit_reasons: List[str] = []
    hr_field = spec.get("hit_reasons_field")
    if hr_field and isinstance(fs.get(hr_field), list):
        hit_reasons.extend(str(x) for x in fs[hr_field])
        watch_field = hr_field.replace("_hit_reasons", "_watch_hit_reasons")
        if isinstance(fs.get(watch_field), list):
            for x in fs[watch_field]:
                if str(x) not in hit_reasons:
                    hit_reasons.append(str(x))
    if not hit_reasons:
        hit_reasons = _split_rule_hits_by_strategy(rule_hits).get(strategy, [])

    anchor_dates = _extract_anchor_dates(hit_reasons)

    required = spec.get("required", ())
    missing = [f for f in required if _is_missing(fs.get(f))]
    data_quality = "insufficient" if missing else "sufficient"

    return {
        "run_id": run_id,
        "run_trade_date": run_row["trade_date"],
        "strategy": strategy,
        "strategy_in_matched": strategy in matched,
        "matched_strategies": matched,
        "meta": {
            "code": rec.get("code"),
            "name": rec.get("name"),
            "rank": rec.get("rank"),
            "close": fs.get("close"),
            "is_st": (None if _is_missing(fs.get("is_st")) else bool(fs.get("is_st"))),
            "setup_type": rec.get("setup_type"),
            "trade_stage": rec.get("trade_stage"),
            "entry_maturity": rec.get("entry_maturity"),
            "risk_level": rec.get("risk_level"),
            "selected_for_ai": bool(rec.get("selected_for_ai")),
        },
        "regime": {
            "market_regime": rec.get("market_regime"),
            "theme_position": rec.get("theme_position"),
        },
        "common_snapshot": common_snapshot,
        "strategy_snapshot": strategy_snapshot,
        "hit_reasons": hit_reasons,
        "anchor_dates": anchor_dates,
        "data_quality": data_quality,
        "missing_fields": missing,
        "dsa_ai_conclusion": {
            "ai_summary": rec.get("ai_summary"),
            "ai_reasoning": rec.get("ai_reasoning"),
            "ai_operation_advice": rec.get("ai_operation_advice"),
            "ai_trade_stage": rec.get("ai_trade_stage"),
            "ai_confidence": rec.get("ai_confidence"),
        },
    }


# 日线列白名单（防 SQL 注入：动态列名仅允许以下取值）
_ALLOWED_DAILY_COLUMNS = (
    "date", "open", "high", "low", "close", "volume", "amount", "pct_chg",
    "ma5", "ma10", "ma20", "volume_ratio", "data_source", "adj_factor", "adj_anchor_date",
)
_DEFAULT_DAILY_COLUMNS = (
    "date", "open", "high", "low", "close", "volume", "amount", "pct_chg",
    "ma5", "ma10", "ma20", "volume_ratio", "data_source",
)


def _parse_daily_columns(columns: Any) -> List[str]:
    if not columns:
        return list(_DEFAULT_DAILY_COLUMNS)
    if isinstance(columns, str):
        req = [c.strip() for c in columns.split(",") if c.strip()]
    elif isinstance(columns, (list, tuple)):
        req = [str(c).strip() for c in columns]
    else:
        return list(_DEFAULT_DAILY_COLUMNS)
    out = [c for c in req if c in _ALLOWED_DAILY_COLUMNS]
    if "date" not in out:
        out.insert(0, "date")
    return out or list(_DEFAULT_DAILY_COLUMNS)


def _parse_anchor_list(anchor_dates: Any) -> List[str]:
    if not anchor_dates:
        return []
    if isinstance(anchor_dates, str):
        raw = [a.strip() for a in anchor_dates.split(",")]
    elif isinstance(anchor_dates, (list, tuple)):
        raw = [str(a).strip() for a in anchor_dates]
    else:
        return []
    return [a for a in raw if _DATE_RE.fullmatch(a)]


def query_stock_daily(
    code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Any = 60,
    columns: Any = None,
    anchor_dates: Any = None,
    window: Any = 3,
) -> Dict[str, Any]:
    if not code:
        raise ValueError("code 为必需参数")
    limit = _to_int(limit, 60, minimum=1, maximum=500)
    window = _to_int(window, 3, minimum=1, maximum=20)
    cols = _parse_daily_columns(columns)
    col_sql = ", ".join(cols)  # 安全：cols 已对白名单过滤

    where = ["code = ?"]
    params: List[Any] = [code.strip()]
    if start_date:
        where.append("date >= ?")
        params.append(start_date.strip())
    if end_date:
        where.append("date <= ?")
        params.append(end_date.strip())
    clause = " AND ".join(where)

    anchors = _parse_anchor_list(anchor_dates)
    if anchors:
        # 锚点模式：取最近 500 根（倒序取、正序出，确保最新及锚点不被截断），
        # 再按锚点切 ±window 窗口。锚点为近期结构点（P1/P2/P3、A/B 等），必在此范围内。
        fetch_sql = (
            f"SELECT {col_sql} FROM (SELECT {col_sql} FROM stock_daily WHERE {clause} "
            "ORDER BY date DESC LIMIT 500) ORDER BY date ASC"
        )
        with _connect() as conn:
            rows = conn.execute(fetch_sql, params).fetchall()
        items = [_row_to_dict(r) for r in rows]
        dates = [it["date"] for it in items]
        n = len(items)
        keep: set = set()
        for a in anchors:
            pos: Optional[int] = None
            for i, d in enumerate(dates):
                if d <= a:
                    pos = i
                else:
                    break
            if pos is None and n:
                pos = 0
            if pos is not None:
                for j in range(max(0, pos - window), min(n, pos + window + 1)):
                    keep.add(j)
        # 附带最近窗口（最新走势）
        for j in range(max(0, n - window * 2), n):
            keep.add(j)
        sliced = [items[j] for j in sorted(keep)]
        return {
            "code": code.strip(),
            "mode": "anchor",
            "window": window,
            "anchors": anchors,
            "total": len(sliced),
            "daily": sliced,
        }

    # 普通模式：取最近 limit 条（倒序取、正序出）
    sql = (
        f"SELECT {col_sql} FROM (SELECT {col_sql} FROM stock_daily WHERE {clause} "
        "ORDER BY date DESC LIMIT ?) ORDER BY date ASC"
    )
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [_row_to_dict(r) for r in rows]
    return {"code": code.strip(), "mode": "recent", "total": len(items), "daily": items}


def get_stock_info(code: str) -> Dict[str, Any]:
    if not code:
        raise ValueError("code 为必需参数")
    with _connect() as conn:
        row = conn.execute(
            "SELECT code, name, market, exchange, listing_status, is_st, industry, "
            "list_date FROM instrument_master WHERE code = ?",
            (code.strip(),),
        ).fetchone()
    if row is None:
        raise ValueError(f"未找到股票主数据: {code}")
    info = _row_to_dict(row)
    info["is_st"] = bool(info.get("is_st"))
    return info


# ----------------------------------------------------------------------------
# 选股前瞻验证（evaluate_selection）
# ----------------------------------------------------------------------------
# 团队定位为"选股 + 买卖点验证器"而非交易执行模拟器：用后续日线走势回答
# ①选股准不准 ②买卖点准不准 ③体系赚不赚钱。本命令负责【选股质量镜头】——
# 以选股当日(或之前最近交易日)收盘价为基准，度量每只候选后续 N 个交易日的
# 前瞻收益、最大有利/不利波动(MFE/MAE)。与"能否成交/封板买入"完全无关，
# 全票纳入（含无结构化买点的强势/打板类，补齐 PaperTrader 成交模拟覆盖不到的盲区）。
# "能实际吃到的钱"由 PaperTrader 的确定性成交模拟另行度量，两镜头分开、互不污染。

_DEFAULT_EVAL_HORIZONS = (1, 3, 5, 10)


def _parse_horizons(horizons: Any) -> List[int]:
    if not horizons:
        return list(_DEFAULT_EVAL_HORIZONS)
    if isinstance(horizons, str):
        raw = [h.strip() for h in re.split(r"[,\s]+", horizons) if h.strip()]
    elif isinstance(horizons, (list, tuple)):
        raw = [str(h).strip() for h in horizons]
    else:
        return list(_DEFAULT_EVAL_HORIZONS)
    out: List[int] = []
    for h in raw:
        try:
            v = int(h)
        except (ValueError, TypeError):
            continue
        if 1 <= v <= 60 and v not in out:
            out.append(v)
    return sorted(out) or list(_DEFAULT_EVAL_HORIZONS)


def _parse_code_list(codes: Any) -> List[str]:
    if not codes:
        return []
    if isinstance(codes, str):
        raw = [c.strip() for c in re.split(r"[,\s]+", codes) if c.strip()]
    elif isinstance(codes, (list, tuple)):
        raw = [str(c).strip() for c in codes if str(c).strip()]
    else:
        return []
    seen: List[str] = []
    for c in raw:
        if c not in seen:
            seen.append(c)
    return seen


def _agg_forward(rows: List[Dict[str, Any]], horizons: List[int]) -> Dict[str, Any]:
    ev = [it for it in rows if it.get("evaluable")]
    agg: Dict[str, Any] = {"count": len(rows), "evaluable": len(ev)}
    for k in horizons:
        key = f"T+{k}"
        vals = [it["forward_returns_pct"][key] for it in ev
                if it.get("forward_returns_pct", {}).get(key) is not None]
        if vals:
            agg[key] = {
                "avg_ret_pct": round(sum(vals) / len(vals), 2),
                "win_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                "n": len(vals),
            }
        else:
            agg[key] = None
    mfes = [it["mfe_pct"] for it in ev if it.get("mfe_pct") is not None]
    maes = [it["mae_pct"] for it in ev if it.get("mae_pct") is not None]
    if mfes:
        agg["avg_mfe_pct"] = round(sum(mfes) / len(mfes), 2)
    if maes:
        agg["avg_mae_pct"] = round(sum(maes) / len(maes), 2)
    return agg


def evaluate_selection(
    run_id: str,
    codes: Any = None,
    horizons: Any = None,
    limit: Any = 100,
) -> Dict[str, Any]:
    """选股前瞻验证（选股质量镜头，纯日线、确定性）。

    以选股当日(或之前最近交易日)收盘价为基准，统计每只候选后续 N 个交易日的
    前瞻收益(T+k 收盘相对基准的涨跌幅)、MFE(最大有利波动)、MAE(最大不利波动)，
    并给出整体与分策略汇总(均值/胜率)。与能否成交/封板买入无关，全票纳入。
    """
    if not run_id:
        raise ValueError("run_id 为必需参数")
    hs = _parse_horizons(horizons)
    max_h = max(hs)
    limit = _to_int(limit, 100, minimum=1, maximum=500)
    want_codes = _parse_code_list(codes)

    items: List[Dict[str, Any]] = []
    with _connect() as conn:
        run_row = conn.execute(
            "SELECT run_id, trade_date, market, status FROM screening_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f"选股批次不存在: {run_id}")
        run_trade_date = run_row["trade_date"]

        cand_where = ["run_id = ?"]
        cand_params: List[Any] = [run_id]
        if want_codes:
            placeholders = ",".join(["?"] * len(want_codes))
            cand_where.append(f"code IN ({placeholders})")
            cand_params.extend(want_codes)
        cand_sql = (
            "SELECT code, name, rank, setup_type, matched_strategies_json "
            "FROM screening_candidates WHERE " + " AND ".join(cand_where) +
            " ORDER BY rank ASC LIMIT ?"
        )
        cand_params.append(limit)
        cand_rows = conn.execute(cand_sql, cand_params).fetchall()

        for r in cand_rows:
            code = r["code"]
            matched = _safe_json_load(r["matched_strategies_json"]) or []
            ref_row = conn.execute(
                "SELECT date, close FROM stock_daily WHERE code = ? AND date <= ? "
                "ORDER BY date DESC LIMIT 1",
                (code, run_trade_date),
            ).fetchone()
            if ref_row is None or _is_missing(ref_row["close"]) or not ref_row["close"]:
                items.append({
                    "code": code, "name": r["name"], "rank": r["rank"],
                    "setup_type": r["setup_type"], "matched_strategies": matched,
                    "evaluable": False, "note": "无选股日及之前的行情基准价，无法评估",
                })
                continue
            ref_close = float(ref_row["close"])
            fwd_rows = conn.execute(
                "SELECT date, open, high, low, close FROM stock_daily "
                "WHERE code = ? AND date > ? ORDER BY date ASC LIMIT ?",
                (code, run_trade_date, max_h),
            ).fetchall()
            fwd: List[Dict[str, Any]] = []
            for b in fwd_rows:
                o, h, l, c = b["open"], b["high"], b["low"], b["close"]
                if any(_is_missing(x) for x in (o, h, l, c)):
                    continue  # 停牌/缺数据日跳过
                fwd.append({"date": b["date"], "open": float(o), "high": float(h),
                            "low": float(l), "close": float(c)})
            n_fwd = len(fwd)
            fwd_returns: Dict[str, Any] = {}
            for k in hs:
                fwd_returns[f"T+{k}"] = (
                    round((fwd[k - 1]["close"] - ref_close) / ref_close * 100, 2)
                    if n_fwd >= k else None
                )
            mfe = mae = next_open_gap = None
            if n_fwd > 0:
                mfe = round((max(b["high"] for b in fwd) - ref_close) / ref_close * 100, 2)
                mae = round((min(b["low"] for b in fwd) - ref_close) / ref_close * 100, 2)
                next_open_gap = round((fwd[0]["open"] - ref_close) / ref_close * 100, 2)
            items.append({
                "code": code, "name": r["name"], "rank": r["rank"],
                "setup_type": r["setup_type"], "matched_strategies": matched,
                "evaluable": n_fwd > 0,
                "ref_date": ref_row["date"], "ref_close": round(ref_close, 4),
                "forward_bars": n_fwd,
                "next_open_gap_pct": next_open_gap,
                "forward_returns_pct": fwd_returns,
                "mfe_pct": mfe, "mae_pct": mae,
                "last_forward_date": fwd[-1]["date"] if n_fwd else None,
            })

    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        for s in (it.get("matched_strategies") or ["_unmatched"]):
            by_strategy.setdefault(s, []).append(it)

    return {
        "run_id": run_id,
        "run_trade_date": run_trade_date,
        "horizons": hs,
        "reference": "基准=选股当日(或之前最近交易日)收盘价；前瞻收益(T+k)=后续第k个交易日收盘相对基准涨跌幅%",
        "note": "选股质量镜头：与能否成交/封板买入无关，全票纳入(含无结构化买点的强势/打板类)；"
                "能实际吃到的盈利由 PaperTrader 成交模拟另计，两镜头分开。前瞻收益为概率线索非单票定论。",
        "summary": _agg_forward(items, hs),
        "by_strategy": {s: _agg_forward(rows, hs) for s, rows in by_strategy.items()},
        "items": items,
    }


# ----------------------------------------------------------------------------
# 板块查询（board_master / instrument_board_membership 只读）
# ----------------------------------------------------------------------------
# 与 DSA 的 DatabaseManager.list_active_boards_with_member_count /
# batch_get_board_member_codes 等价（同一张 SQLite 库），仅做只读 SQL，不复用
# DSA 的 HTTP/ORM 层。

def list_boards(
    market: Optional[str] = None,
    board_type: Optional[str] = None,
    min_member_count: Any = 0,
) -> Dict[str, Any]:
    """列出指定市场下的活跃板块及成员股票数量（按成员数降序）。"""
    mk = (market or "cn").strip() or "cn"
    min_count = _to_int(min_member_count, 0, minimum=0, maximum=100000)
    where = ["bm.is_active = 1", "bm.market = ?"]
    params: List[Any] = [mk]
    if board_type:
        where.append("bm.board_type = ?")
        params.append(board_type.strip())
    having = ""
    if min_count > 0:
        having = " HAVING COUNT(ibm.id) >= ?"
    sql = (
        "SELECT bm.id AS board_id, bm.board_name, bm.board_type, "
        "COUNT(ibm.id) AS member_count "
        "FROM board_master bm "
        "LEFT JOIN instrument_board_membership ibm ON ibm.board_id = bm.id "
        "WHERE " + " AND ".join(where) +
        " GROUP BY bm.id, bm.board_name, bm.board_type" + having +
        " ORDER BY member_count DESC"
    )
    if min_count > 0:
        params.append(min_count)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [_row_to_dict(r) for r in rows]
    return {
        "market": mk,
        "board_type": board_type,
        "total": len(items),
        "boards": items,
    }


def get_board_constituents(
    board_name: str,
    market: Optional[str] = None,
) -> Dict[str, Any]:
    """查询指定板块的成分股代码与名称（板块→股票反向查询）。"""
    if not board_name:
        raise ValueError("board_name 为必需参数")
    mk = (market or "cn").strip() or "cn"
    name = board_name.strip()
    sql = (
        "SELECT ibm.instrument_code AS code, im.name AS name "
        "FROM instrument_board_membership ibm "
        "JOIN board_master bm ON ibm.board_id = bm.id "
        "LEFT JOIN instrument_master im "
        "  ON im.code = ibm.instrument_code AND im.market = ibm.market "
        "WHERE bm.board_name = ? AND bm.is_active = 1 AND ibm.market = ? "
        "ORDER BY ibm.instrument_code"
    )
    with _connect() as conn:
        rows = conn.execute(sql, (name, mk)).fetchall()
    # 同一票可能因多 source 重复，去重保名
    seen: set = set()
    items: List[Dict[str, Any]] = []
    for r in rows:
        code = r["code"]
        if not code or code in seen:
            continue
        seen.add(code)
        items.append({"code": code, "name": r["name"]})
    return {
        "market": mk,
        "board_name": name,
        "total": len(items),
        "codes": [it["code"] for it in items],
        "constituents": items,
    }
