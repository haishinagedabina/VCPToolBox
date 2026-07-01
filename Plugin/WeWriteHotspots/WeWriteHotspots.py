"""WeWriteHotspots — wechat-public-account topic radar.

Thin VCP wrapper around wewrite/scripts/fetch_hotspots.py. Spawns the
upstream script as a subprocess (network-isolated) and re-emits its JSON
verbatim plus a couple of VCP-friendly fields (`skip_reason` etc).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402


def _resolve_limit(args: dict) -> int:
    raw = bootstrap.pick(args, "limit", "Limit", "count", "n",
                         default=os.environ.get("WEWRITE_HOTSPOTS_DEFAULT_LIMIT", "20"))
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"limit 必须是整数, 收到: {raw!r}")
    if n < 1:
        n = 1
    if n > 50:
        n = 50
    return n


def fetch(args: dict) -> dict:
    bootstrap.ensure_runtime()

    limit = _resolve_limit(args)
    script = bootstrap.SCRIPTS_DIR / "fetch_hotspots.py"
    if not script.exists():
        raise FileNotFoundError(f"upstream script missing: {script}")

    proc = bootstrap.run_python(script, ["--limit", str(limit)], timeout=30)

    if proc.returncode != 0:
        raise RuntimeError(
            f"fetch_hotspots.py exit={proc.returncode} stderr={proc.stderr.strip()[:300]}"
        )

    raw_out = proc.stdout.strip()
    if not raw_out:
        raise RuntimeError("fetch_hotspots.py emitted empty stdout")

    try:
        payload = json.loads(raw_out)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"fetch_hotspots.py stdout not JSON: {e}; head={raw_out[:200]!r}"
        ) from e

    # Promote the upstream "error" field (used when all 3 sources fail) to a
    # VCP-friendlier `skip_reason` so the agent's degradation logic can hit on
    # a stable key.
    upstream_err = payload.pop("error", None)
    if upstream_err:
        payload["skip_reason"] = upstream_err
        payload.setdefault(
            "agent_hint",
            "all three platforms failed — fall back to {{VCPDailyHot}} or {{VCPVSearch}}",
        )

    if proc.stderr.strip() and os.environ.get("DebugMode", "").lower() in ("true", "1"):
        payload["_warnings"] = proc.stderr.strip().splitlines()

    return payload


def main() -> None:
    try:
        args = bootstrap.read_args()
        cmd = str(bootstrap.pick(args, "command", "action", default="Fetch")).strip().lower()
        if cmd in ("", "fetch", "hotspots", "list", "today"):
            bootstrap.success(fetch(args))
        else:
            bootstrap.failure(
                f"未知命令 {cmd!r}。当前仅支持: Fetch。",
                code="UNKNOWN_COMMAND",
            )
    except ValueError as e:
        bootstrap.failure(str(e), code="INVALID_ARGS")
    except FileNotFoundError as e:
        bootstrap.failure(str(e), code="MISSING_UPSTREAM_SCRIPT")
    except Exception as e:  # noqa: BLE001
        bootstrap.failure(
            f"WeWriteHotspots 内部错误: {e}",
            code="INTERNAL_ERROR",
        )


if __name__ == "__main__":
    main()
