"""WeWriteRender — Markdown → 微信公众号 HTML.

VCP synchronous plugin. Reads a JSON arg blob from stdin, invokes the
unmodified `wewrite/toolkit/converter.py`, and writes a JSON envelope to
stdout. All heavy lifting lives in WeWriteCore; this module only handles
argument normalization and the VCP protocol envelope.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
from pathlib import Path

# Wire up the shared bootstrap module (it lives in Plugin/WeWriteCore/).
_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402


def _safe_slug(text: str) -> str:
    """Build a filesystem-safe slug. Windows console codepages frequently
    cannot encode CJK file names; we fall back to a short hash when the
    input is mostly non-ASCII."""
    base = (text or "").strip().replace(" ", "-")
    ascii_part = re.sub(r"[^A-Za-z0-9._-]+", "", base)
    if ascii_part and len(ascii_part) >= 3:
        return ascii_part[:80]
    digest = hashlib.sha1((base or "wewrite").encode("utf-8")).hexdigest()[:8]
    return f"wewrite-{int(time.time())}-{digest}"


def _normalize_command(args: dict) -> str:
    cmd = bootstrap.pick(args, "command", "action", "Command", default="Render")
    return str(cmd).strip() or "Render"


def _resolve_theme(args: dict) -> str:
    requested = bootstrap.pick(args, "theme", "Theme", "themeName", "theme_name")
    if requested:
        return str(requested).strip()
    import os
    return os.environ.get("WEWRITE_DEFAULT_THEME", "professional-clean")


def render(args: dict) -> dict:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()

    # Import lazily so a missing wewrite dep gives a clear error message.
    from converter import WeChatConverter  # type: ignore[import-not-found]
    from theme import load_theme  # type: ignore[import-not-found]

    md_value = bootstrap.pick(args, "markdown", "Markdown", "content", "text", "input")
    if not md_value:
        raise ValueError("必需参数 `markdown` 缺失。请提供 Markdown 原文、本地路径或 file:// URL。")

    md_path = bootstrap.materialize_markdown(str(md_value))
    theme_name = _resolve_theme(args)

    try:
        theme = load_theme(theme_name)
    except FileNotFoundError:
        raise ValueError(
            f"未知排版主题: {theme_name!r}。"
            f"调用 WeWriteThemes 查看可用主题列表。"
        )

    converter = WeChatConverter(theme=theme)
    result = converter.convert_file(str(md_path))

    output_payload: dict = {
        "title": result.title,
        "digest": result.digest,
        "html": result.html,
        "images": list(result.images),
        "theme": theme.name,
        "theme_description": theme.description,
        "char_count": len(result.html),
    }

    save_html = bool(bootstrap.pick(args, "save_html", "save", default=False))
    if save_html:
        out_path = bootstrap.SKILL_DIR / "output" / f"{_safe_slug(result.title or md_path.stem)}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.html, encoding="utf-8")
        output_payload["output_path"] = str(out_path)

    return output_payload


def main() -> None:
    try:
        args = bootstrap.read_args()
        command = _normalize_command(args)

        if command.lower() == "render":
            result = render(args)
        else:
            bootstrap.failure(
                f"未知命令 {command!r}。当前仅支持: Render。",
                code="UNKNOWN_COMMAND",
            )
            return

        bootstrap.success(result)
    except FileNotFoundError as e:
        if getattr(e, "code", None) == "FILE_NOT_FOUND_LOCALLY":
            bootstrap.emit_file_not_found(e)
            return
        bootstrap.failure(str(e), code="FILE_NOT_FOUND")
    except ValueError as e:
        bootstrap.failure(str(e), code="INVALID_ARGS")
    except Exception as e:  # noqa: BLE001 — last-resort guard for VCP protocol
        bootstrap.failure(f"WeWriteRender 内部错误: {e}", code="INTERNAL_ERROR")


if __name__ == "__main__":
    main()
