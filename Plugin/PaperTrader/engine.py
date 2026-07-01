# -*- coding: utf-8 -*-
"""PaperTrader 模拟引擎：前向逐日确定性模拟。

决策约定（已与用户确认）：
- 从 run_trade_date 向后逐日推进（不在选股当日成交）。
- 买点：仅当 low<=trigger<=high（盘中确实到过该价）才以 trigger_price 成交；跳空越过则当日不成交（不追高）。
- 同一日同时触发止损与止盈 → 优先止损（保守）。
- 跳空低于止损 → 以开盘价成交（更差，保守）。
- 最多 MAX_SLOTS 仓，watching(待触发) 也占仓。
- 止盈分批按 take_profits；尾仓 trailing_final 用"跌破前一交易日最低价"清仓；首次止盈后止损上移保本。
- watching 超过 invalidation_days 个交易日仍未触发首笔 → 失效平仓(0 盈亏)。

盈亏会计：每仓分配 capital；买点档 ratio*capital 按 trigger 成交得份额；
止盈/清仓按成交价回收；realized_pnl 累计；pnl_pct = realized_pnl / invested。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import db


def _round(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), n)


def normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    entries = []
    for e in plan.get("entries", []) or []:
        tp = e.get("trigger_price")
        if tp is None:
            continue
        entries.append({
            "trigger_price": float(tp),
            "ratio": float(e.get("ratio", 0.34)),
            "label": str(e.get("label", "买点")),
        })
    if not entries:
        raise ValueError("plan.entries 为空或无有效 trigger_price")
    tps = []
    for t in plan.get("take_profits", []) or []:
        if t.get("gain_pct") is None:
            continue
        tps.append({"gain_pct": float(t["gain_pct"]), "reduce_frac": float(t.get("reduce_frac", 0.34))})
    return {
        "entries": entries,
        "stop_loss": (None if plan.get("stop_loss") is None else float(plan["stop_loss"])),
        "take_profits": tps,
        "trailing_final": bool(plan.get("trailing_final", True)),
        "invalidation_days": int(plan.get("invalidation_days", 3)),
        "source": plan.get("source", "explicit"),
    }


def build_new_position(code: str, name: str, strategy: str, run_id: str,
                       run_trade_date: str, plan: Dict[str, Any], capital: float) -> Dict[str, Any]:
    plan = normalize_plan(plan)
    entries_state = [{**e, "filled": False, "fill_date": None, "fill_price": None,
                      "shares": 0.0, "cost": 0.0} for e in plan["entries"]]
    tp_state = [{**t, "hit": False, "hit_date": None, "price": None} for t in plan["take_profits"]]
    return {
        "code": code.strip(),
        "name": name or "",
        "strategy": strategy,
        "run_id": run_id,
        "run_trade_date": run_trade_date,
        "status": "watching",
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "entries_json": json.dumps(entries_state, ensure_ascii=False),
        "tp_json": json.dumps(tp_state, ensure_ascii=False),
        "capital": float(capital),
        "shares": 0.0,
        "invested": 0.0,
        "avg_cost": 0.0,
        "realized_pnl": 0.0,
        "cur_stop": plan["stop_loss"],
        "prior_low": None,
        "bars_watched": 0,
        "all_tp_hit": 0,
        "sim_through_date": run_trade_date,   # 从次个交易日开始模拟
        "first_fill_date": None,
        "exit_date": None,
        "exit_reason": None,
    }


def _archive(conn, pos: Dict[str, Any], avg_exit: Optional[float], exit_date: str,
             reason: str) -> None:
    invested = float(pos.get("invested") or 0)
    pnl_amount = float(pos.get("realized_pnl") or 0)
    pnl_pct = round(pnl_amount / invested * 100, 2) if invested > 0 else 0.0
    holding_days = 0
    if pos.get("first_fill_date"):
        try:
            d0 = datetime.fromisoformat(pos["first_fill_date"])
            d1 = datetime.fromisoformat(exit_date)
            holding_days = (d1 - d0).days
        except (ValueError, TypeError):
            holding_days = 0
    db.archive_trade(conn, {
        "code": pos["code"], "name": pos.get("name"), "strategy": pos.get("strategy"),
        "run_id": pos.get("run_id"),
        "entry_date": pos.get("first_fill_date"), "avg_entry": _round(pos.get("avg_cost")),
        "exit_date": exit_date, "avg_exit": _round(avg_exit),
        "holding_days": holding_days,
        "capital": _round(pos.get("capital")), "invested": _round(invested),
        "pnl_amount": _round(pnl_amount, 2), "pnl_pct": pnl_pct,
        "exit_reason": reason,
        "closed_at": datetime.now().isoformat(timespec="seconds"),
    })


def simulate(conn, pos: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    """把单个持仓从 sim_through_date 推进到 target_date，返回本次动作列表。"""
    actions: List[Dict[str, Any]] = []
    if pos["status"] == "exited":
        return {"code": pos["code"], "actions": actions, "status": "exited"}

    plan = json.loads(pos["plan_json"])
    entries = json.loads(pos["entries_json"])
    tps = json.loads(pos["tp_json"])

    shares = float(pos["shares"] or 0)
    invested = float(pos["invested"] or 0)
    avg_cost = float(pos["avg_cost"] or 0)
    realized = float(pos["realized_pnl"] or 0)
    cur_stop = pos["cur_stop"]
    prior_low = pos["prior_low"]
    bars_watched = int(pos["bars_watched"] or 0)
    all_tp_hit = int(pos["all_tp_hit"] or 0)
    status = pos["status"]
    first_fill = pos["first_fill_date"]
    capital = float(pos["capital"])
    invalidation_days = int(plan.get("invalidation_days", 3))
    trailing_final = bool(plan.get("trailing_final", True))

    bars = db.fetch_daily(pos["code"], pos["sim_through_date"], target_date, limit=600)
    bars = [b for b in bars if b["date"] > pos["sim_through_date"]]

    exited = False
    exit_reason = None
    exit_date = None
    last_exit_price = None

    for bar in bars:
        d = bar["date"]
        o, h, l = _f(bar["open"]), _f(bar["high"]), _f(bar["low"])
        if None in (o, h, l):
            continue  # 停牌/缺数据，跳过当日

        # 1) 入场触发（low<=trigger<=high 才以 trigger 成交）
        for e in entries:
            if e["filled"]:
                continue
            trig = e["trigger_price"]
            if l <= trig <= h:
                cash = capital * e["ratio"]
                sh = cash / trig
                shares += sh
                invested += cash
                avg_cost = invested / shares if shares > 0 else 0
                e.update({"filled": True, "fill_date": d, "fill_price": trig,
                          "shares": round(sh, 2), "cost": round(cash, 2)})
                status = "holding"
                if not first_fill:
                    first_fill = d
                actions.append({"date": d, "action": "建仓", "label": e["label"],
                                "price": trig, "ratio": e["ratio"]})

        # 时间止损：仍未成交且 watching 超期
        if status == "watching":
            bars_watched += 1
            if bars_watched > invalidation_days:
                exited, exit_reason, exit_date = True, "未触发失效", d
                actions.append({"date": d, "action": "失效平仓", "reason": "超过invalidation_days未触发买点"})
                break
            prior_low = l
            continue

        # 2) 止损（优先于止盈）
        if shares > 0 and cur_stop is not None:
            if o <= cur_stop:               # 跳空低开穿越 → 开盘价成交
                fill = o
            elif l <= cur_stop:
                fill = cur_stop
            else:
                fill = None
            if fill is not None:
                realized += shares * fill - shares * avg_cost
                actions.append({"date": d, "action": "止损清仓", "price": round(fill, 4),
                                "shares": round(shares, 2)})
                shares = 0
                exited, exit_reason, exit_date, last_exit_price = True, "止损", d, fill
                break

        # 3) 分批止盈（按档位，目标价 = 均价*(1+pct%)）
        if shares > 0:
            for t in tps:
                if t["hit"]:
                    continue
                target = avg_cost * (1 + t["gain_pct"] / 100.0)
                if h >= target:
                    sold = shares * t["reduce_frac"]
                    realized += sold * target - sold * avg_cost
                    shares -= sold
                    t.update({"hit": True, "hit_date": d, "price": round(target, 4)})
                    last_exit_price = target
                    actions.append({"date": d, "action": "止盈减仓", "gain_pct": t["gain_pct"],
                                    "price": round(target, 4), "reduce_frac": t["reduce_frac"]})
                    # 首次止盈后止损上移保本
                    if cur_stop is None or cur_stop < avg_cost:
                        cur_stop = round(avg_cost, 4)
            if all(t["hit"] for t in tps):
                all_tp_hit = 1

        # 4) 尾仓移动止损：所有止盈档命中后，跌破前一交易日最低价清仓
        if shares > 0 and all_tp_hit and trailing_final and prior_low is not None:
            if l < prior_low:
                fill = o if o < prior_low else prior_low
                realized += shares * fill - shares * avg_cost
                actions.append({"date": d, "action": "尾仓清仓", "reason": "跌破前一交易日最低价",
                                "price": round(fill, 4), "shares": round(shares, 2)})
                shares = 0
                exited, exit_reason, exit_date, last_exit_price = True, "尾仓跌破前低清仓", d, fill
                break

        prior_low = l

    # 落库
    sim_through = bars[-1]["date"] if bars else pos["sim_through_date"]
    if exited:
        _archive(conn, {**pos, "shares": 0, "invested": invested, "avg_cost": avg_cost,
                        "realized_pnl": realized, "first_fill_date": first_fill,
                        "capital": capital}, last_exit_price, exit_date or sim_through, exit_reason)
        db.update_position(conn, pos["id"], {
            "status": "exited", "shares": 0, "invested": round(invested, 2),
            "avg_cost": round(avg_cost, 4), "realized_pnl": round(realized, 2),
            "entries_json": json.dumps(entries, ensure_ascii=False),
            "tp_json": json.dumps(tps, ensure_ascii=False),
            "exit_date": exit_date or sim_through, "exit_reason": exit_reason,
            "sim_through_date": sim_through, "first_fill_date": first_fill,
        })
    else:
        db.update_position(conn, pos["id"], {
            "status": status, "shares": round(shares, 4), "invested": round(invested, 2),
            "avg_cost": round(avg_cost, 4), "realized_pnl": round(realized, 2),
            "cur_stop": cur_stop, "prior_low": prior_low, "bars_watched": bars_watched,
            "all_tp_hit": all_tp_hit, "first_fill_date": first_fill,
            "entries_json": json.dumps(entries, ensure_ascii=False),
            "tp_json": json.dumps(tps, ensure_ascii=False),
            "sim_through_date": sim_through,
        })

    return {"code": pos["code"], "name": pos.get("name"), "strategy": pos.get("strategy"),
            "actions": actions, "status": "exited" if exited else status,
            "exit_reason": exit_reason}


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN 检测
    except (ValueError, TypeError):
        return None


def portfolio_view(conn) -> Dict[str, Any]:
    positions = db.open_positions(conn)
    out = []
    total_unrealized = 0.0
    for p in positions:
        view = {
            "code": p["code"], "name": p["name"], "strategy": p["strategy"],
            "status": p["status"], "run_trade_date": p["run_trade_date"],
            "avg_cost": p["avg_cost"], "shares": p["shares"],
            "invested": p["invested"], "realized_pnl": p["realized_pnl"],
            "cur_stop": p["cur_stop"], "sim_through_date": p["sim_through_date"],
        }
        unrealized = 0.0
        if (p["shares"] or 0) > 0:
            lc = db.latest_close(p["code"])
            if lc and lc.get("close"):
                unrealized = (float(lc["close"]) - float(p["avg_cost"])) * float(p["shares"])
                view["last_close"] = lc["close"]
                view["last_date"] = lc["date"]
        view["unrealized_pnl"] = round(unrealized, 2)
        inv = float(p["invested"] or 0)
        view["total_pnl_pct"] = round((float(p["realized_pnl"] or 0) + unrealized) / inv * 100, 2) if inv > 0 else 0.0
        total_unrealized += unrealized
        out.append(view)
    return {
        "positions": out,
        "open_count": len(out),
        "max_slots": int(os_env_int("MAX_SLOTS", 3)),
        "free_slots": max(0, int(os_env_int("MAX_SLOTS", 3)) - len(out)),
        "total_unrealized_pnl": round(total_unrealized, 2),
    }


def compute_stats(conn) -> Dict[str, Any]:
    trades = db.trade_history(conn, limit=100000)
    closed = [t for t in trades if t.get("exit_reason") != "未触发失效"]
    n = len(closed)
    wins = [t for t in closed if (t.get("pnl_amount") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_amount") or 0) < 0]
    total_pnl = sum(float(t.get("pnl_amount") or 0) for t in closed)
    gross_win = sum(float(t["pnl_amount"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl_amount"]) for t in losses))
    by_strategy: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        s = t.get("strategy") or "unknown"
        g = by_strategy.setdefault(s, {"trades": 0, "wins": 0, "pnl_amount": 0.0})
        g["trades"] += 1
        g["wins"] += 1 if (t.get("pnl_amount") or 0) > 0 else 0
        g["pnl_amount"] += float(t.get("pnl_amount") or 0)
    for s, g in by_strategy.items():
        g["win_rate"] = round(g["wins"] / g["trades"] * 100, 1) if g["trades"] else 0.0
        g["pnl_amount"] = round(g["pnl_amount"], 2)
    pv = portfolio_view(conn)
    return {
        "closed_trades": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "total_realized_pnl": round(total_pnl, 2),
        "avg_pnl_pct": round(sum(float(t.get("pnl_pct") or 0) for t in closed) / n, 2) if n else 0.0,
        "avg_holding_days": round(sum(int(t.get("holding_days") or 0) for t in closed) / n, 1) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_gain": round(max((float(t.get("pnl_amount") or 0) for t in closed), default=0), 2),
        "max_loss": round(min((float(t.get("pnl_amount") or 0) for t in closed), default=0), 2),
        "invalidated_count": len([t for t in trades if t.get("exit_reason") == "未触发失效"]),
        "by_strategy": by_strategy,
        "open_positions": pv["open_count"],
        "open_unrealized_pnl": pv["total_unrealized_pnl"],
    }


def os_env_int(key: str, default: int) -> int:
    import os
    try:
        return int(os.environ.get(key, str(default)) or default)
    except (ValueError, TypeError):
        return default
