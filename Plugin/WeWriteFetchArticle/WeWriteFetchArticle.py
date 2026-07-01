"""WeWriteFetchArticle — extract any WeChat article URL into clean Markdown.

Thin VCP wrapper around `wewrite/scripts/fetch_article.py`. Imports
`fetch_article()` directly since the upstream module is pure stdlib +
requests/BeautifulSoup; the Camoufox/Playwright fallbacks are imported
lazily inside `fetch_html()` so this plugin still loads even when those
heavy browser deps are missing.

Default behaviour mirrors WeWriteCore's environment hardening:
  - HTTP_PROXY / HTTPS_PROXY are stripped before requests imports.
  - Set `allow_proxy: true` per-call OR `WEWRITE_ALLOW_PROXY=1` globally
    to disable that stripping (rarely needed for mp.weixin.qq.com).

Quill uses this in three workflows:
  1. "学一篇" — fetch a benchmark article → feed into WeWriteHumanness
  2. "建范文库" — fetch + WeWriteExemplar.Extract to grow the corpus
  3. "回测自己" — fetch own previously published article → diff with the
     local draft via WeWriteLearnEdits
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any
from datetime import datetime

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

# IMPORTANT: import bootstrap BEFORE deciding whether to honour proxies, so
# its default proxy-stripping kicks in unless the caller explicitly opts in.
import bootstrap  # noqa: E402


def _restore_proxy_env(saved: dict[str, str]) -> None:
    for k, v in saved.items():
        os.environ[k] = v
    for k in ("NO_PROXY", "no_proxy"):
        if os.environ.get(k) == "*":
            del os.environ[k]


def _maybe_allow_proxy(args: dict) -> dict[str, str]:
    """If caller passes allow_proxy=true, restore inherited proxy env.

    bootstrap.py has already deleted HTTP_PROXY / HTTPS_PROXY / ALL_PROXY by
    this point. We try to re-read them from the parent process snapshot
    captured before import time. As a last resort, fall back to the values
    that Node's server.js inherited (which we have no direct access to), so
    in practice this only re-enables proxies if WEWRITE_ALLOW_PROXY was set
    in the launching shell. The runtime allow_proxy flag mostly skips the
    NO_PROXY=* wildcard which actively blocks requests.

    Returns the snapshot of what we changed so the caller can rollback.
    """
    flag = bootstrap.pick(args, "allow_proxy", "use_proxy", default=False)
    if not flag or str(flag).lower() in ("0", "false", "no", "off"):
        return {}

    saved: dict[str, str] = {}
    for k in ("NO_PROXY", "no_proxy"):
        if os.environ.get(k) == "*":
            saved[k] = "*"
            del os.environ[k]
    return saved


def _safe_slug(s: str, fallback: str = "article") -> str:
    """ASCII-safe slug from title for use as a filename (CJK-safe by trimming
    to first 6 word-ish chars OR a hex digest fallback)."""
    if not s:
        return fallback
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"[^\w\-]", "", s, flags=re.UNICODE)
    if not s:
        return fallback
    import hashlib
    if any(ord(c) > 127 for c in s):
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]
    return s[:40] or fallback


def fetch(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()

    url = bootstrap.pick(args, "url", "article_url", "wechat_url")
    file_path = bootstrap.pick(args, "file_path", "path", "html_path", "file")

    if not url and not file_path:
        raise ValueError(
            "必需参数缺失: 请传 `url` (微信文章 URL) 或 `file_path` (本地 HTML 路径) 二选一。"
        )

    # File-path mode resolves through bootstrap.materialize_markdown to
    # handle file:// URLs and existing local files. We pass the resolved
    # path directly to fetch_article(file_path=...).
    resolved_file: str | None = None
    if file_path:
        if isinstance(file_path, str) and file_path.startswith("file://"):
            from urllib.parse import urlparse, unquote
            parsed = urlparse(file_path)
            raw_path = unquote(parsed.path)
            if os.name == "nt" and raw_path.startswith("/") and re.match(r"^/[A-Za-z]:", raw_path):
                raw_path = raw_path[1:]
            local = Path(raw_path)
            if not (local.exists() and local.is_file()):
                err = FileNotFoundError(f"file_path: local file not found: {file_path}")
                err.code = "FILE_NOT_FOUND_LOCALLY"  # type: ignore[attr-defined]
                err.fileUrl = file_path  # type: ignore[attr-defined]
                err.failedParameter = "file_path"  # type: ignore[attr-defined]
                raise err
            resolved_file = str(local)
        else:
            local = Path(str(file_path))
            if not (local.exists() and local.is_file()):
                raise FileNotFoundError(f"file_path 文件不存在: {file_path}")
            resolved_file = str(local)

    saved_proxy = _maybe_allow_proxy(args)
    try:
        from fetch_article import fetch_article, _fetch_requests, _fetch_camoufox, _fetch_playwright, _has_content  # type: ignore[import-not-found]

        # We do not use fetch_article() directly when fetching by URL,
        # because the upstream `fetch_html()` calls sys.exit(1) on failure
        # which would terminate the whole plugin without giving us a
        # chance to surface a structured VCP error. Instead we replicate
        # its three-level fallback here so we can capture which level
        # actually succeeded.
        if resolved_file:
            result = fetch_article(file_path=resolved_file)
            fetched_via = "file"
            html: str | None = None  # we don't have the raw HTML in this branch
        else:
            html = None
            fetched_via = ""
            timeout = int(os.environ.get("WEWRITE_FETCH_TIMEOUT", "25") or "25")

            html = _fetch_requests(str(url), timeout=timeout)
            if html and _has_content(html):
                fetched_via = "requests"
            else:
                html = _fetch_camoufox(str(url))
                if html and _has_content(html):
                    fetched_via = "camoufox"
                else:
                    html = _fetch_playwright(str(url))
                    if html and _has_content(html):
                        fetched_via = "playwright"

            if not html or not fetched_via:
                raise RuntimeError(
                    "三级抓取全部失败 (requests / camoufox / playwright)。"
                    "建议在浏览器中打开该 URL → 右键 → 另存为 HTML → "
                    "再以 file_path 参数重新调用本插件。"
                    "也可能是 URL 已失效, 或被微信反爬拦截。"
                )

            # Reuse upstream parser on the captured HTML
            from bs4 import BeautifulSoup
            from fetch_article import _extract_metadata, html_to_markdown
            soup = BeautifulSoup(html, "html.parser")
            meta = _extract_metadata(soup)
            md = html_to_markdown(soup)
            result = {
                "title": meta["title"],
                "author": meta["author"],
                "publish_time": meta["publish_time"],
                "markdown": md,
                "url": str(url),
            }
    finally:
        _restore_proxy_env(saved_proxy)

    markdown_text = result.get("markdown") or ""
    char_count = len(re.sub(r"\s+", "", markdown_text))

    payload: dict[str, Any] = {
        "title": result.get("title", ""),
        "author": result.get("author", ""),
        "publish_time": result.get("publish_time", ""),
        "url": result.get("url", ""),
        "markdown": markdown_text,
        "char_count": char_count,
        "fetched_via": fetched_via,
    }

    # Optional corpus persistence
    save_to_corpus = bootstrap.pick(args, "save_to_corpus", "save", "persist", default=False)
    if save_to_corpus and str(save_to_corpus).lower() not in ("0", "false", "no", "off"):
        corpus_dir = bootstrap.SKILL_DIR / "corpus"
        corpus_dir.mkdir(parents=True, exist_ok=True)
        slug = _safe_slug(result.get("title") or "")
        date_prefix = ""
        pub_time = result.get("publish_time") or ""
        m = re.search(r"(\d{4})[\-/年](\d{1,2})[\-/月](\d{1,2})", pub_time)
        if m:
            date_prefix = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}-"
        else:
            date_prefix = datetime.now().strftime("%Y-%m-%d-")

        out_file = corpus_dir / f"{date_prefix}{slug}.md"
        # Avoid clobbering by appending a counter
        counter = 1
        while out_file.exists():
            out_file = corpus_dir / f"{date_prefix}{slug}-{counter}.md"
            counter += 1

        fm_lines = ["---", f"title: {result.get('title', '')!r}"]
        if result.get("author"):
            fm_lines.append(f"author: {result['author']!r}")
        if result.get("publish_time"):
            fm_lines.append(f"date: {result['publish_time']!r}")
        if result.get("url"):
            fm_lines.append(f"source: {result['url']!r}")
        fm_lines.append("---")
        out_file.write_text("\n".join(fm_lines) + "\n\n" + markdown_text, encoding="utf-8")
        payload["corpus_path"] = str(out_file)

    include_html = bootstrap.pick(args, "include_html", "with_html", default=False)
    if include_html and str(include_html).lower() not in ("0", "false", "no", "off"):
        # html is set only in URL branch; file branch reads HTML inside upstream
        if "html" in locals() and html:
            payload["html"] = html

    return payload


def main() -> None:
    try:
        args = bootstrap.read_args()
        cmd = str(bootstrap.pick(args, "command", "action", default="Fetch")).strip().lower()
        if cmd in ("", "fetch", "get", "extract"):
            bootstrap.success(fetch(args))
        else:
            bootstrap.failure(
                f"未知命令 {cmd!r}。当前仅支持: Fetch。",
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
        bootstrap.failure(
            f"WeWriteFetchArticle 内部错误: {e}",
            code="INTERNAL_ERROR",
        )


if __name__ == "__main__":
    main()
