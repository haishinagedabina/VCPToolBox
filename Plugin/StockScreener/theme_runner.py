# -*- coding: utf-8 -*-
"""
DSA 选股触发层（写/触发，独立于只读的 db_reader）。

trigger_board_run（板块锁定选股·唯一触发路径）：把 board_names 经只读库解析成成分股
代码，作为限定选股宇宙 POST 给 ``/api/v1/screening/runs``(stock_codes 限定)，DSA 只在
该宇宙内选股——这才是"传入板块=选股宇宙"。返回 run_id 供老牛流水线接力。

注：DSA 的 /openclaw-theme-run 接口跑全市场、题材仅作软打分不锁宇宙（会选出与板块无关
的票），故本插件不提供该软路径，统一只用板块锁定。

选股是 DSA 端同步阻塞调用（跑完才返回，约 152-197s）；仅做触发与结果回传，
不在本地做选股/排序。只用标准库 urllib，不引入新依赖。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List


class ThemeRunConfigError(Exception):
    """DSA API 基址未配置。"""


_RUNS_PATH = "/api/v1/screening/runs"


def _resolve_base_url() -> str:
    raw = os.environ.get("DSA_API_BASE_URL", "").strip()
    if not raw:
        raise ThemeRunConfigError(
            "未配置 DSA_API_BASE_URL。请在 Plugin/StockScreener/config.env 中设置 DSA "
            "服务基址（如 http://127.0.0.1:8000），用于触发热点题材选股。"
        )
    return raw.rstrip("/")


def _post_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """向 DSA POST 一个 JSON，返回 {http_status, json}。统一错误处理。"""
    base = _resolve_base_url()
    url = base + path
    # 选股是同步阻塞调用(DSA 跑完才返回，约 152-197s)，默认须大于该耗时；
    # 实际值由 config.env 的 DSA_API_TIMEOUT 覆盖(默认 540)。
    timeout = float(os.environ.get("DSA_API_TIMEOUT", "540") or 540)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    cookie = os.environ.get("DSA_API_COOKIE", "").strip()
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"http_status": resp.status, "json": json.loads(body) if body.strip() else {}}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise ValueError(f"DSA 返回错误 HTTP {exc.code}: {detail or exc.reason}（接口 {url}）")
    except urllib.error.URLError as exc:
        raise ValueError(
            f"无法连接 DSA 服务 {url}: {exc.reason}。请确认 DSA 已启动且 DSA_API_BASE_URL 正确。"
        )


def _get_json(path: str) -> Dict[str, Any]:
    """向 DSA GET 一个 JSON，返回 {http_status, json}。失败抛 ValueError。"""
    base = _resolve_base_url()
    url = base + path
    timeout = float(os.environ.get("DSA_API_TIMEOUT", "540") or 540)
    req = urllib.request.Request(url, method="GET")
    cookie = os.environ.get("DSA_API_COOKIE", "").strip()
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"http_status": resp.status, "json": json.loads(body) if body.strip() else {}}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise ValueError(f"DSA 返回错误 HTTP {exc.code}: {detail or exc.reason}（接口 {url}）")
    except urllib.error.URLError as exc:
        raise ValueError(
            f"无法连接 DSA 服务 {url}: {exc.reason}。请确认 DSA 已启动且 DSA_API_BASE_URL 正确。"
        )


def _parse_names(value: Any) -> List[str]:
    """解析名称列表：list 原样；JSON 数组字符串先 json.loads；否则按逗号拆。"""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except (ValueError, TypeError):
                pass
        return [x.strip() for x in s.split(",") if x.strip()]
    return [str(value).strip()]


def _to_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        result = int(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return default
    return max(minimum, min(maximum, result))


def trigger_board_run(
    board_names: Any = None,
    stock_codes: Any = None,
    trade_date: Any = None,
    market: Any = "cn",
    mode: Any = "balanced",
    candidate_limit: Any = 5,
    ai_top_k: Any = 5,
    strategies: Any = None,
) -> Dict[str, Any]:
    """到最热的题材板块去选股（书：借板块势，让题材真正驱动"选谁"）。

    板块锁定选股：把 board_names 经只读库解析成板块成分股代码，连同显式 stock_codes
    一起作为【限定选股宇宙】POST 给 DSA 的 /runs 接口（stock_codes 限定宇宙）。
    DSA 只在这些成分股范围内跑策略选股——这才是"传入板块=选股宇宙"。返回 run_id。

    注：DSA 的 /openclaw-theme-run 接口写死 stock_codes=None 跑全市场、题材只作软打分，
    不锁宇宙，故本函数不走 openclaw，改走 /runs 显式股票池来真正锁定板块。
    """
    _resolve_base_url()  # 提前校验配置
    mk = (str(market).strip() or "cn") if market else "cn"
    if mk != "cn":
        raise ValueError("market 当前仅支持 cn（DSA phase 1）")

    boards = _parse_names(board_names)
    codes: List[str] = _parse_names(stock_codes)

    cand_limit = _to_int(candidate_limit, 5, minimum=1, maximum=200)
    top_k = _to_int(ai_top_k, 5, minimum=0, maximum=50)
    if top_k > cand_limit:
        raise ValueError("ai_top_k 不能大于 candidate_limit")

    # 解析板块名 → 成分股代码（只读库），并入选股宇宙
    resolved_from_boards: Dict[str, int] = {}
    if boards:
        import db_reader
        for b in boards:
            try:
                res = db_reader.get_board_constituents(board_name=b, market=mk)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"解析板块成分股失败（{b}）: {exc}")
            bcodes = res.get("codes", []) or []
            resolved_from_boards[b] = len(bcodes)
            codes.extend(bcodes)

    # 去重保序
    seen: set = set()
    uniq_codes: List[str] = []
    for c in codes:
        c = str(c).strip()
        if c and c not in seen:
            seen.add(c)
            uniq_codes.append(c)
    if not uniq_codes:
        raise ValueError(
            "未解析到任何成分股。请提供 board_names（题材板块名，将取其成分股为选股宇宙）或 stock_codes。"
            + ("（板块均无成分股：" + json.dumps(resolved_from_boards, ensure_ascii=False) + "）" if boards else "")
        )

    mode_str = str(mode).strip() if mode else "balanced"
    if mode_str not in {"balanced", "aggressive", "quality"}:
        raise ValueError("mode 必须是 balanced/aggressive/quality 之一")

    runs_payload: Dict[str, Any] = {
        "market": mk,
        "stock_codes": uniq_codes,
        "mode": mode_str,
        "candidate_limit": cand_limit,
        "ai_top_k": top_k,
        "rerun_failed": False,
    }
    if trade_date:
        runs_payload["trade_date"] = str(trade_date).strip()
    strat_list = _parse_names(strategies)
    if strat_list:
        runs_payload["strategies"] = strat_list

    resp = _post_json(_RUNS_PATH, runs_payload)
    dsa = resp["json"] if isinstance(resp.get("json"), dict) else {}
    result: Dict[str, Any] = {
        "triggered": True,
        "http_status": resp["http_status"],
        "universe_mode": "board_restricted",
        "board_names": boards,
        "board_member_counts": resolved_from_boards,
        "resolved_code_count": len(uniq_codes),
        "sample_codes": uniq_codes[:10],
        "strategies": strat_list or None,
        "note": "板块成分股作为限定选股宇宙经 /runs 选股；universe_size 应等于 resolved_code_count，候选只会出自该宇宙。",
        "dsa_response": dsa,
    }
    # 回查本次 run 的实际锁定宇宙规模，核对"只在板块成分股内选股"（失败不影响主结果）
    run_id = dsa.get("run_id") if isinstance(dsa, dict) else None
    if run_id:
        try:
            detail = _get_json(f"{_RUNS_PATH}/{run_id}")
            dj = detail.get("json") if isinstance(detail.get("json"), dict) else {}
            if isinstance(dj, dict):
                result["universe_size"] = dj.get("universe_size")
                result["candidate_count"] = dj.get("candidate_count")
        except ValueError:
            pass
    return result
