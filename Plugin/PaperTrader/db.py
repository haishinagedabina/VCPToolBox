# -*- coding: utf-8 -*-
"""PaperTrader 数据层。

两类连接：
- DSA 行情库（只读, mode=ro）：取 stock_daily 日线做模拟成交。
- 模拟盘账本库（读写, 自动建表）：positions(当前持仓) + trades_history(已平仓)。

所有写入走单连接事务；stdout 由入口统一输出 JSON，本层不打印。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class DbConfigError(Exception):
    """数据库路径配置缺失或文件不存在。"""


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------

def _stock_db_path() -> str:
    raw = os.environ.get("STOCK_DB_PATH", "").strip()
    if not raw:
        raise DbConfigError("未配置 STOCK_DB_PATH（DSA 行情库路径）。")
    p = Path(raw)
    if not p.exists():
        raise DbConfigError(f"STOCK_DB_PATH 不存在: {p}")
    return str(p.absolute())


def _paper_db_path() -> str:
    raw = os.environ.get("PAPER_DB_PATH", "").strip()
    if not raw:
        raise DbConfigError("未配置 PAPER_DB_PATH（模拟盘账本路径）。")
    return str(Path(raw).absolute())


def _timeout() -> float:
    return float(os.environ.get("STOCK_DB_TIMEOUT", "5") or 5)


def connect_stock() -> sqlite3.Connection:
    uri = f"file:{Path(_stock_db_path()).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=_timeout())
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(_timeout() * 1000)}")
    return conn


def connect_paper() -> sqlite3.Connection:
    path = _paper_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_timeout())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(_timeout() * 1000)}")
    _ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 账本 schema
# ---------------------------------------------------------------------------

def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            strategy TEXT,
            run_id TEXT,
            run_trade_date TEXT,
            status TEXT NOT NULL DEFAULT 'watching',   -- watching/holding/exited
            added_at TEXT,
            plan_json TEXT,            -- 归一化买卖剧本
            entries_json TEXT,         -- 各档买点成交状态
            tp_json TEXT,              -- 各止盈档命中状态
            capital REAL,              -- 本仓分配资金
            shares REAL DEFAULT 0,     -- 当前持有份额
            invested REAL DEFAULT 0,   -- 累计已投入资金（成交档）
            avg_cost REAL DEFAULT 0,   -- 持仓均价
            realized_pnl REAL DEFAULT 0,
            cur_stop REAL,             -- 当前（可移动）止损价
            prior_low REAL,            -- 前一交易日最低价（尾仓跌破清仓用）
            bars_watched INTEGER DEFAULT 0,  -- 仍处 watching 已经过的交易日数（时间止损用）
            all_tp_hit INTEGER DEFAULT 0,
            sim_through_date TEXT,     -- 已模拟到的交易日
            first_fill_date TEXT,
            exit_date TEXT,
            exit_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS trades_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, name TEXT, strategy TEXT, run_id TEXT,
            entry_date TEXT, avg_entry REAL,
            exit_date TEXT, avg_exit REAL,
            holding_days INTEGER,
            capital REAL, invested REAL,
            pnl_amount REAL, pnl_pct REAL,
            exit_reason TEXT,
            closed_at TEXT
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 行情读取（DSA, 只读）
# ---------------------------------------------------------------------------

def fetch_daily(code: str, start_date: str, end_date: Optional[str] = None,
                limit: int = 400) -> List[Dict[str, Any]]:
    """取 [start_date, end_date] 的日线（升序）。用于前向模拟。"""
    where = ["code = ?", "date >= ?"]
    params: List[Any] = [code.strip(), start_date]
    if end_date:
        where.append("date <= ?")
        params.append(end_date)
    sql = (
        "SELECT date, open, high, low, close, volume_ratio, adj_factor "
        "FROM stock_daily WHERE " + " AND ".join(where) +
        " ORDER BY date ASC LIMIT ?"
    )
    params.append(limit)
    with connect_stock() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def latest_close(code: str, on_or_before: Optional[str] = None) -> Optional[Dict[str, Any]]:
    where = ["code = ?"]
    params: List[Any] = [code.strip()]
    if on_or_before:
        where.append("date <= ?")
        params.append(on_or_before)
    sql = ("SELECT date, close FROM stock_daily WHERE " + " AND ".join(where) +
           " ORDER BY date DESC LIMIT 1")
    with connect_stock() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def fetch_candidate_snapshot(run_id: str, code: str) -> Dict[str, Any]:
    """从 DSA 取该候选的 factor_snapshot（用于自动提取买卖剧本）。"""
    with connect_stock() as conn:
        row = conn.execute(
            "SELECT candidate_decision_json, factor_snapshot_json, name "
            "FROM screening_candidates WHERE run_id = ? AND code = ?",
            (run_id, code.strip()),
        ).fetchone()
    if row is None:
        return {}
    rec = dict(row)
    fs: Dict[str, Any] = {}
    cd = rec.get("candidate_decision_json")
    if cd:
        try:
            obj = json.loads(cd)
            if isinstance(obj, dict) and isinstance(obj.get("factor_snapshot"), dict):
                fs = obj["factor_snapshot"]
        except (ValueError, TypeError):
            pass
    if not fs and rec.get("factor_snapshot_json"):
        try:
            obj = json.loads(rec["factor_snapshot_json"])
            if isinstance(obj, dict):
                fs = obj
        except (ValueError, TypeError):
            pass
    fs.setdefault("name", rec.get("name"))
    return fs


def run_trade_date(run_id: str) -> Optional[str]:
    with connect_stock() as conn:
        row = conn.execute(
            "SELECT trade_date FROM screening_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return row["trade_date"] if row else None


def list_run_candidates(run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """按 run 批量列候选（code/name/rank + 命中策略），供买卖点质量批量评估。只读。"""
    with connect_stock() as conn:
        rows = conn.execute(
            "SELECT code, name, rank, matched_strategies_json FROM screening_candidates "
            "WHERE run_id = ? ORDER BY rank ASC LIMIT ?",
            (run_id, int(limit)),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        ms: List[str] = []
        raw = r["matched_strategies_json"]
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    ms = [str(x) for x in loaded]
            except (ValueError, TypeError):
                ms = []
        out.append({"code": r["code"], "name": r["name"], "rank": r["rank"],
                    "matched_strategies": ms})
    return out


# ---------------------------------------------------------------------------
# 持仓 CRUD
# ---------------------------------------------------------------------------

def open_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM positions WHERE status != 'exited' ORDER BY id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def count_open(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM positions WHERE status != 'exited'"
    ).fetchone()
    return int(row["c"])


def position_by_code(conn: sqlite3.Connection, code: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM positions WHERE code = ? AND status != 'exited'", (code.strip(),)
    ).fetchone()
    return dict(row) if row else None


def insert_position(conn: sqlite3.Connection, pos: Dict[str, Any]) -> int:
    cols = ", ".join(pos.keys())
    ph = ", ".join("?" for _ in pos)
    cur = conn.execute(f"INSERT INTO positions ({cols}) VALUES ({ph})", list(pos.values()))
    conn.commit()
    return int(cur.lastrowid)


def update_position(conn: sqlite3.Connection, pid: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE positions SET {sets} WHERE id = ?", list(fields.values()) + [pid])
    conn.commit()


def archive_trade(conn: sqlite3.Connection, trade: Dict[str, Any]) -> None:
    cols = ", ".join(trade.keys())
    ph = ", ".join("?" for _ in trade)
    conn.execute(f"INSERT INTO trades_history ({cols}) VALUES ({ph})", list(trade.values()))
    conn.commit()


def trade_history(conn: sqlite3.Connection, limit: int = 100,
                  strategy: Optional[str] = None) -> List[Dict[str, Any]]:
    where = ""
    params: List[Any] = []
    if strategy:
        where = " WHERE strategy = ?"
        params.append(strategy)
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM trades_history{where} ORDER BY exit_date DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]
