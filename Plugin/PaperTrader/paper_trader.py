# -*- coding: utf-8 -*-
"""PaperTrader 插件入口（VCP synchronous / stdio）。

从 stdin 读 JSON，按 command 路由；stdout 只输出唯一 JSON 信封。
命令：get_portfolio / add_position / run_eod_update / close_position /
      get_trade_history / get_stats。
"""

import io
import json
import math
import os
import sys
from datetime import datetime

try:
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import db
import engine
import plan_extractor
import timing_eval


def _sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _read_args() -> dict:
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("插件输入必须是 JSON 对象")
    return data


def _max_slots() -> int:
    try:
        return int(os.environ.get("MAX_SLOTS", "3") or 3)
    except (ValueError, TypeError):
        return 3


def _position_capital() -> float:
    try:
        return float(os.environ.get("POSITION_CAPITAL", "100000") or 100000)
    except (ValueError, TypeError):
        return 100000.0


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def cmd_add_position(args: dict):
    code = str(args.get("code", "")).strip()
    strategy = str(args.get("strategy", "")).strip()
    run_id = str(args.get("run_id", "")).strip()
    if not code or not strategy:
        raise ValueError("add_position 需要 code 与 strategy")

    with db.connect_paper() as conn:
        if db.count_open(conn) >= _max_slots():
            return {"rejected": True, "reason": f"已满 {_max_slots()} 仓，需等空槽（卖出后）再补"}
        if db.position_by_code(conn, code):
            return {"rejected": True, "reason": f"{code} 已在追踪中，不重复加入"}

    # 取买卖剧本：优先显式 plan，否则从 DSA 自动提取
    plan = args.get("plan")
    if isinstance(plan, str) and plan.strip():
        plan = json.loads(plan)
    name = str(args.get("name", "")).strip()
    rtd = str(args.get("run_trade_date", "")).strip()
    if not isinstance(plan, dict):
        if not run_id:
            raise ValueError("未提供 plan 时必须给 run_id，以便从 DSA 自动提取买卖剧本")
        fs = db.fetch_candidate_snapshot(run_id, code)
        if not fs:
            return {"rejected": True, "reason": f"DSA 中找不到候选 run_id={run_id} code={code}"}
        plan = plan_extractor.build_plan(fs, strategy)
        if not plan:
            return {"rejected": True, "reason": f"{code}({strategy}) 无结构化买点，无法加入（请换票或显式传 plan）"}
        name = name or str(fs.get("name") or "")
    if not rtd and run_id:
        rtd = db.run_trade_date(run_id) or _today()
    rtd = rtd or _today()

    pos = engine.build_new_position(code, name, strategy, run_id, rtd, plan, _position_capital())
    with db.connect_paper() as conn:
        pid = db.insert_position(conn, pos)
        # 立即向后模拟到今天，让新仓即时反映真实进度
        row = dict(conn.execute("SELECT * FROM positions WHERE id = ?", (pid,)).fetchone())
        sim = engine.simulate(conn, row, _today())
    return {"added": True, "code": code, "strategy": strategy, "plan_source": plan.get("source"),
            "run_trade_date": rtd, "initial_sim": sim}


def cmd_run_eod(args: dict):
    target = str(args.get("trade_date", "")).strip() or _today()
    results = []
    with db.connect_paper() as conn:
        for pos in db.open_positions(conn):
            results.append(engine.simulate(conn, pos, target))
        view = engine.portfolio_view(conn)
    changed = [r for r in results if r.get("actions")]
    return {"trade_date": target, "updated": len(results),
            "actions": changed, "portfolio": view}


def cmd_close(args: dict):
    code = str(args.get("code", "")).strip()
    reason = str(args.get("reason", "手动平仓")).strip()
    if not code:
        raise ValueError("close_position 需要 code")
    with db.connect_paper() as conn:
        pos = db.position_by_code(conn, code)
        if not pos:
            return {"closed": False, "reason": f"{code} 不在持仓中"}
        shares = float(pos["shares"] or 0)
        avg_cost = float(pos["avg_cost"] or 0)
        realized = float(pos["realized_pnl"] or 0)
        exit_price = None
        if shares > 0:
            lc = db.latest_close(pos["code"])
            exit_price = float(lc["close"]) if lc and lc.get("close") else avg_cost
            realized += shares * exit_price - shares * avg_cost
        engine._archive(conn, {**pos, "realized_pnl": realized}, exit_price, _today(), reason)
        db.update_position(conn, pos["id"], {
            "status": "exited", "shares": 0, "realized_pnl": round(realized, 2),
            "exit_date": _today(), "exit_reason": reason})
    return {"closed": True, "code": code, "reason": reason}


def _dispatch(args: dict):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError(
            "缺少 command 参数。可用命令: get_portfolio, add_position, run_eod_update, "
            "close_position, get_trade_history, get_stats, evaluate_timing")

    if command == "get_portfolio":
        with db.connect_paper() as conn:
            return engine.portfolio_view(conn)
    if command == "add_position":
        return cmd_add_position(args)
    if command == "run_eod_update":
        return cmd_run_eod(args)
    if command == "close_position":
        return cmd_close(args)
    if command == "get_trade_history":
        with db.connect_paper() as conn:
            return {"trades": db.trade_history(conn,
                    limit=int(args.get("limit", 100) or 100),
                    strategy=(str(args.get("strategy")).strip() or None) if args.get("strategy") else None)}
    if command == "get_stats":
        with db.connect_paper() as conn:
            return engine.compute_stats(conn)
    if command == "evaluate_timing":
        return timing_eval.evaluate_timing(
            run_id=str(args.get("run_id", "")).strip(),
            codes=args.get("codes"),
            horizons=args.get("horizons"),
            limit=args.get("limit", 100),
        )
    raise ValueError(f"未知 command: {command}")


def main() -> None:
    try:
        args = _read_args()
        result = _dispatch(args)
        output = {"status": "success", "result": result}
    except db.DbConfigError as exc:
        output = {"status": "error", "error": f"配置错误: {exc}"}
    except ValueError as exc:
        output = {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        output = {"status": "error", "error": f"插件执行失败: {exc}"}

    print(json.dumps(_sanitize(output), ensure_ascii=False, default=str))
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
