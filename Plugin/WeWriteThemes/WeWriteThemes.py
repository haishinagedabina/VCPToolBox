"""WeWriteThemes — list / preview WeChat layout themes.

Two commands:
  - ListThemes  → 列出 16+ 主题及主色板 (轻量, 直接读 YAML)
  - Gallery     → 调用 wewrite 自带 cli.py gallery 渲染一份并排对比的 HTML

Both stay read-only relative to the embedded wewrite source.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402


THEME_CATEGORIES: dict[str, list[str]] = {
    "通用": ["professional-clean", "minimal", "newspaper"],
    "科技": ["tech-modern", "bytedance", "github"],
    "文艺": ["warm-editorial", "sspai", "ink", "elegant-rose"],
    "商务": ["bold-navy", "minimal-gold", "bold-green"],
    "风格": ["bauhaus", "focus-red", "midnight"],
}


def _list_themes() -> dict:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()

    from theme import list_themes, load_theme  # type: ignore[import-not-found]

    names = list_themes()
    themes: list[dict] = []
    for name in names:
        t = load_theme(name)
        colors = t.colors or {}
        themes.append({
            "name": t.name,
            "description": t.description,
            "primary": colors.get("primary", ""),
            "text": colors.get("text", ""),
            "background": colors.get("background", ""),
        })

    known = set(names)
    categories = {
        label: [n for n in members if n in known]
        for label, members in THEME_CATEGORIES.items()
    }
    uncategorised = sorted(known - {n for ns in categories.values() for n in ns})
    if uncategorised:
        categories["其他"] = uncategorised

    return {
        "count": len(themes),
        "themes": themes,
        "categories": categories,
    }


def _gallery(args: dict) -> dict:
    """Use wewrite's own cli.py gallery to keep behaviour identical."""
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()

    from cli import _build_gallery_html, _gallery_sample_markdown  # type: ignore[import-not-found]
    from concurrent.futures import ThreadPoolExecutor
    from converter import WeChatConverter  # type: ignore[import-not-found]
    from theme import list_themes, load_theme  # type: ignore[import-not-found]

    md_value = bootstrap.pick(args, "markdown", "Markdown", "content", "text", "input")
    if md_value:
        md_path = bootstrap.materialize_markdown(str(md_value))
        md_text = md_path.read_text(encoding="utf-8")
    else:
        md_text = _gallery_sample_markdown()

    names = list_themes()

    def _render(name: str):
        t = load_theme(name)
        c = WeChatConverter(theme=t)
        r = c.convert(md_text)
        return name, t.description, r.html

    results: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, desc, html in pool.map(_render, names):
            results[name] = (desc, html)

    gallery_html = _build_gallery_html(results, names)

    save_html = bootstrap.pick(args, "save_html", "save", default=True)
    output_path: str | None = None
    if save_html:
        out = bootstrap.SKILL_DIR / "output" / "wewrite-gallery.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(gallery_html, encoding="utf-8")
        output_path = str(out)

    return {
        "html": gallery_html,
        "themes": names,
        "output_path": output_path,
    }


def main() -> None:
    try:
        args = bootstrap.read_args()
        command = str(bootstrap.pick(args, "command", "action", default="ListThemes")).lower()

        if command == "listthemes" or command == "list" or command == "themes":
            bootstrap.success(_list_themes())
        elif command == "gallery":
            bootstrap.success(_gallery(args))
        else:
            bootstrap.failure(
                f"未知命令 {command!r}。当前支持: ListThemes / Gallery。",
                code="UNKNOWN_COMMAND",
            )

    except FileNotFoundError as e:
        if getattr(e, "code", None) == "FILE_NOT_FOUND_LOCALLY":
            bootstrap.emit_file_not_found(e)
            return
        bootstrap.failure(str(e), code="FILE_NOT_FOUND")
    except ValueError as e:
        bootstrap.failure(str(e), code="INVALID_ARGS")
    except Exception as e:  # noqa: BLE001
        bootstrap.failure(f"WeWriteThemes 内部错误: {e}", code="INTERNAL_ERROR")


if __name__ == "__main__":
    main()
