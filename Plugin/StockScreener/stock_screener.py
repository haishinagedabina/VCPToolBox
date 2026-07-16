# -*- coding: utf-8 -*-
"""
StockScreener 插件入口（VCP synchronous / stdio）。

从 stdin 读取 JSON 参数，按 command 路由到 db_reader 的只读查询，
最终向 stdout 打印唯一一个 JSON 信封 {"status": "...", "result"/"error": ...}。

纪律：stdout 只输出最终 JSON；任何调试/日志一律走 stderr，否则会污染
VCP 对返回结果的 JSON 解析。
"""

import io
import json
import math
import sys

# Windows / 中文环境强制 UTF-8，避免 cp936 编码导致中文崩溃
try:
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import db_reader
import theme_runner


def _sanitize(obj):
    """递归把 NaN/Infinity 转成 None。

    标准 JSON 不支持 NaN/Infinity，而 DSA 存储的因子快照 JSON 里含有这些值，
    若原样输出，VCP 的 Node 端 JSON.parse 会解析失败导致整个调用崩溃。
    """
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


def _dispatch(args: dict):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError(
            "缺少 command 参数。可用命令: list_screening_runs, get_screening_candidates, "
            "get_candidate_detail, get_strategy_evidence, query_stock_daily, get_stock_info, "
            "list_boards, get_board_constituents, trigger_board_run, evaluate_selection"
        )

    if command == "list_screening_runs":
        return db_reader.list_screening_runs(
            market=args.get("market"),
            status=args.get("status"),
            limit=args.get("limit", 20),
        )
    if command == "get_screening_candidates":
        return db_reader.get_screening_candidates(
            run_id=args.get("run_id", ""),
            limit=args.get("limit", 100),
            with_ai_only=args.get("with_ai_only", False),
        )
    if command == "get_candidate_detail":
        return db_reader.get_candidate_detail(
            run_id=args.get("run_id", ""),
            code=args.get("code", ""),
        )
    if command == "get_strategy_evidence":
        return db_reader.get_strategy_evidence(
            run_id=args.get("run_id", ""),
            code=args.get("code", ""),
            strategy=args.get("strategy", ""),
        )
    if command == "query_stock_daily":
        return db_reader.query_stock_daily(
            code=args.get("code", ""),
            start_date=args.get("start_date"),
            end_date=args.get("end_date"),
            limit=args.get("limit", 60),
            columns=args.get("columns"),
            anchor_dates=args.get("anchor_dates"),
            window=args.get("window", 3),
        )
    if command == "get_stock_info":
        return db_reader.get_stock_info(code=args.get("code", ""))
    if command == "evaluate_selection":
        return db_reader.evaluate_selection(
            run_id=args.get("run_id", ""),
            codes=args.get("codes"),
            horizons=args.get("horizons"),
            limit=args.get("limit", 100),
        )
    if command == "list_boards":
        return db_reader.list_boards(
            market=args.get("market"),
            board_type=args.get("board_type"),
            min_member_count=args.get("min_member_count", 0),
        )
    if command == "get_board_constituents":
        return db_reader.get_board_constituents(
            board_name=args.get("board_name", ""),
            market=args.get("market"),
        )
    if command == "trigger_board_run":
        return theme_runner.trigger_board_run(
            board_names=args.get("board_names"),
            stock_codes=args.get("stock_codes"),
            trade_date=args.get("trade_date"),
            market=args.get("market", "cn"),
            mode=args.get("mode", "balanced"),
            candidate_limit=args.get("candidate_limit", 5),
            ai_top_k=args.get("ai_top_k", 5),
            strategies=args.get("strategies"),
        )

    raise ValueError(f"未知 command: {command}")


def main() -> None:
    try:
        args = _read_args()
        result = _dispatch(args)
        output = {"status": "success", "result": result}
    except (db_reader.DbConfigError, theme_runner.ThemeRunConfigError) as exc:
        output = {"status": "error", "error": f"配置错误: {exc}"}
    except ValueError as exc:
        output = {"status": "error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 兜底，保证始终返回合法信封
        output = {"status": "error", "error": f"插件执行失败: {exc}"}

    print(json.dumps(_sanitize(output), ensure_ascii=False, default=str))
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
