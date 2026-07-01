# -*- coding: utf-8 -*-
"""从 DSA factor_snapshot 提取【归一化买卖剧本】，供模拟引擎确定性执行。

归一化剧本 schema:
{
  "entries": [{"trigger_price": float, "ratio": 0~1, "label": str}],   # 分批买点
  "stop_loss": float,                                                  # 初始止损
  "take_profits": [{"gain_pct": float, "reduce_frac": 0~1}],           # 分批止盈档
  "trailing_final": bool,        # 尾仓"跌破最近低点"清仓
  "invalidation_days": int,      # 未触发首笔则失效
  "source": "rich|fallback"
}

优先用 {prefix}buy_points + {prefix}exit_plan（如底背离双突破，最完整）；
否则回退用 entry_price/stop 字段，或从 hit_reasons 文本解析 P2(入场)/P3(止损)。
若连入场触发价都找不到，返回 None（调用方应换票，不入模拟盘）。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

STRATEGY_PREFIX: Dict[str, str] = {
    "bottom_divergence_double_breakout": "bottom_divergence_",
    "ma100_low123_combined": "ma100_low123_",
    "ma100_60min_combined": "ma100_60min_",
    "shrink_pullback": "shrink_pullback_",
    "trendline_breakout": "trendline_",
    "extreme_strength_combo": "extreme_strength_",
    "gap_limitup_breakout": "",
    "volume_breakout": "",
    "one_yang_three_yin": "",
    "bottom_volume": "",
}

_PRICE_RE = re.compile(r"价格\s*([0-9]+(?:\.[0-9]+)?)")


def _num(v: Any) -> Optional[float]:
    """合法正数才返回，None/NaN/<=0 视为无效。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (ValueError, TypeError):
        return None
    if math.isnan(f) or f <= 0:
        return None
    return f


def _parse_ratio(s: Any) -> float:
    """'1/5仓'→0.2, '1/3仓'→0.333, '半仓'→0.5, '全仓'→1.0。解析不出给 0.34。"""
    if s is None:
        return 0.34
    t = str(s).replace("仓", "").strip()
    if t in ("半", "1/2"):
        return 0.5
    if t in ("全", "满"):
        return 1.0
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", t)
    if m:
        d = int(m.group(2))
        return round(int(m.group(1)) / d, 4) if d else 0.34
    f = _num(t)
    if f is not None and f <= 1:
        return f
    return 0.34


def _reduce_frac(action: Any) -> Optional[float]:
    """止盈动作 → 减仓比例；'清仓/跌破/remaining' 视为尾仓移动止损(返回 None)。"""
    a = str(action or "")
    if any(k in a for k in ("清仓", "跌破", "remaining", "尾仓")):
        return None
    if "半" in a or "1/2" in a:
        return 0.5
    m = re.search(r"1\s*/\s*(\d+)", a)
    if m:
        d = int(m.group(1))
        return round(1 / d, 4) if d else 0.25
    return 0.34


def _from_rich(buy_points: List[Dict[str, Any]], exit_plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for b in buy_points:
        tp = _num(b.get("trigger_price"))
        if tp is None:
            continue
        entries.append({
            "trigger_price": tp,
            "ratio": _parse_ratio(b.get("position_ratio")),
            "label": str(b.get("label") or "买点"),
        })
    if not entries:
        return None
    stop = _num(exit_plan.get("initial_stop_loss")) or _num(buy_points[0].get("stop_loss_price"))
    take_profits: List[Dict[str, Any]] = []
    trailing_final = False
    for t in exit_plan.get("take_profit_targets", []) or []:
        if not isinstance(t, dict):
            continue
        if t.get("remaining") or _reduce_frac(t.get("action")) is None:
            trailing_final = True
            continue
        pct = _num(t.get("pct"))
        if pct is None:
            continue
        take_profits.append({"gain_pct": pct, "reduce_frac": _reduce_frac(t.get("action")) or 0.34})
    if not take_profits:
        take_profits = [{"gain_pct": 10, "reduce_frac": 0.5}, {"gain_pct": 20, "reduce_frac": 0.25}]
        trailing_final = True
    return {
        "entries": entries,
        "stop_loss": stop,
        "take_profits": take_profits,
        "trailing_final": trailing_final,
        "invalidation_days": int(_num(exit_plan.get("invalidation_days")) or 3),
        "source": "rich",
    }


def _text_price(fs: Dict[str, Any], prefix: str, label: str) -> Optional[float]:
    """从 {prefix}hit_reasons / {prefix}watch_hit_reasons 文本里解析 'P2(...,价格X)' 之类。"""
    for key in (f"{prefix}hit_reasons", f"{prefix}watch_hit_reasons"):
        reasons = fs.get(key)
        if not isinstance(reasons, list):
            continue
        for line in reasons:
            s = str(line)
            if label in s:
                m = _PRICE_RE.search(s.split(label, 1)[1][:40])
                if m:
                    return _num(m.group(1))
    return None


def _from_fallback(fs: Dict[str, Any], strategy: str, prefix: str) -> Optional[Dict[str, Any]]:
    """按策略隔离取入场触发价 + 止损（不跨策略串字段）。取不到入场则返回 None。"""
    entry: Optional[float] = None
    stop: Optional[float] = None

    if strategy == "ma100_low123_combined":
        # 低位123：突破 P2 入场，低点3(P3) 止损
        entry = _num(fs.get("pattern_123_entry_price")) or _text_price(fs, "ma100_low123_", "P2")
        stop = _num(fs.get("pattern_123_stop_loss")) or _text_price(fs, "ma100_low123_", "P3")
    elif strategy == "shrink_pullback":
        entry = _num(fs.get("shrink_pullback_entry_price"))
        stop = _num(fs.get("shrink_pullback_stop_loss_price"))
    elif strategy in ("trendline_breakout", "volume_breakout", "gap_limitup_breakout"):
        # 突破类：以关键位/缺口位作为突破触发；止损用结构化止损价
        entry = _num(fs.get("limitup_key_level_price")) or _num(fs.get("gap_key_level_price")) \
            or _num(fs.get("breakaway_gap_high"))
        stop = _num(fs.get("stop_loss_price")) or _num(fs.get("breakaway_gap_low"))
    # 其它策略(extreme_strength_combo/one_yang_three_yin/bottom_volume) 无结构化触发价 → None

    if entry is None:
        return None
    if stop is None:
        stop = round(entry * 0.92, 4)  # 兜底：入场下方 8%
    return {
        "entries": [{"trigger_price": entry, "ratio": 1.0, "label": "入场"}],
        "stop_loss": stop,
        "take_profits": [{"gain_pct": 10, "reduce_frac": 0.5}, {"gain_pct": 20, "reduce_frac": 0.25}],
        "trailing_final": True,
        "invalidation_days": 3,
        "source": "fallback",
    }


def build_plan(factor_snapshot: Dict[str, Any], strategy: str) -> Optional[Dict[str, Any]]:
    prefix = STRATEGY_PREFIX.get(strategy, "")
    bp = factor_snapshot.get(f"{prefix}buy_points") if prefix else None
    ep = factor_snapshot.get(f"{prefix}exit_plan") if prefix else None
    if isinstance(bp, list) and bp and isinstance(ep, dict):
        plan = _from_rich(bp, ep)
        if plan:
            return plan
    return _from_fallback(factor_snapshot, strategy, prefix)
