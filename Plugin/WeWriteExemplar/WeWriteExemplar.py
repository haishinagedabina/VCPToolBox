"""WeWriteExemplar — style exemplar library for SICO-style few-shot injection.

Three commands:
  - Extract: ingest a markdown article → stats fingerprint + 4 segments + category
             → persist to wewrite/references/exemplars/{category}-NNN.md
  - List:    enumerate the library, optionally filtered by category
  - Inject:  for a given framework type, pull the top-N highest-humanness
             exemplars and return a ready-to-splice prompt snippet that Quill
             can paste into its own writing prompt (the "use one human voice,
             not the AI median" trick)

We import `extract_exemplar` and `humanness_score` directly (both pure stdlib
+ yaml) — no subprocess overhead, which matters because Inject is called
once per article-write inside Quill's loop.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402

# Quill's 7 frameworks (matching TVSQuillFrameworks.txt) → exemplar category.
# Multiple frameworks may map to the same category — that's fine.
_FRAMEWORK_TO_CATEGORY: dict[str, str] = {
    # tech-opinion: 数据/具名信源密度高, 适合干货类痛点 + 清单 + 纯观点
    "痛点驱动": "tech-opinion",
    "纯观点": "tech-opinion",
    "对比拆解": "tech-opinion",
    "清单观点": "list-practical",
    # story-emotional: 第一人称叙事 + 情绪 + 回忆
    "故事驱动": "story-emotional",
    "复盘类": "story-emotional",
    # hot-take: 短而锐利的热点解读
    "热点解读": "hot-take",
    # fallbacks for English aliases
    "pain-point": "tech-opinion",
    "story": "story-emotional",
    "list": "list-practical",
    "comparison": "tech-opinion",
    "hot-take": "hot-take",
    "opinion": "tech-opinion",
    "retrospective": "story-emotional",
}

_VALID_CATEGORIES = {
    "tech-opinion",
    "story-emotional",
    "list-practical",
    "hot-take",
    "general",
}


def _resolve_category(framework_raw: str | None, category_raw: str | None) -> str | None:
    """Resolve the requested category from either explicit category or framework alias."""
    if category_raw:
        cat = str(category_raw).strip().lower()
        if cat in _VALID_CATEGORIES:
            return cat
        # Allow Chinese category alias too
        if cat in _FRAMEWORK_TO_CATEGORY:
            return _FRAMEWORK_TO_CATEGORY[cat]
        raise ValueError(
            f"未知 category {category_raw!r}, 候选: {sorted(_VALID_CATEGORIES)}"
        )
    if framework_raw:
        fw = str(framework_raw).strip()
        if fw in _FRAMEWORK_TO_CATEGORY:
            return _FRAMEWORK_TO_CATEGORY[fw]
        # Also accept fuzzy lowercase
        fw_lower = fw.lower()
        for k, v in _FRAMEWORK_TO_CATEGORY.items():
            if k.lower() == fw_lower:
                return v
        raise ValueError(
            f"未知 framework {framework_raw!r}, 候选: {sorted(_FRAMEWORK_TO_CATEGORY.keys())}"
        )
    return None


def _load_markdown(args: dict) -> str:
    content = bootstrap.pick(args, "content", "text", "markdown", "article", "body")
    if content:
        return str(content)
    file_arg = bootstrap.pick(args, "file_path", "path", "input", "file")
    if file_arg:
        md_path = bootstrap.materialize_markdown(str(file_arg))
        return md_path.read_text(encoding="utf-8")
    raise ValueError(
        "必需参数缺失: Extract 命令需要 `content` (Markdown 字符串) 或 `file_path` (文件路径)。"
    )


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

def extract(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import extract_exemplar  # type: ignore[import-not-found]

    text = _load_markdown(args)
    if not text.strip():
        raise ValueError("文章内容为空, 无法入库")

    category_raw = bootstrap.pick(args, "category")
    if category_raw:
        cat = str(category_raw).strip().lower()
        if cat not in _VALID_CATEGORIES:
            # Try to resolve via framework alias
            mapped = _FRAMEWORK_TO_CATEGORY.get(cat) or _FRAMEWORK_TO_CATEGORY.get(str(category_raw).strip())
            if mapped:
                cat = mapped
            else:
                raise ValueError(
                    f"未知 category {category_raw!r}, 候选: {sorted(_VALID_CATEGORIES)}"
                )
        category = cat
    else:
        category = None  # let upstream auto-detect

    source = bootstrap.pick(args, "source", "name", "author") or None

    exemplar = extract_exemplar.extract_exemplar(
        text,
        category=category,
        source=str(source) if source else None,
    )
    saved_path = extract_exemplar.save_exemplar(exemplar)

    return {
        "title": exemplar.get("title", ""),
        "source": exemplar.get("source", ""),
        "category": exemplar.get("category", ""),
        "humanness_score": exemplar.get("humanness_score"),
        "fingerprint": exemplar.get("fingerprint", {}),
        "segments": exemplar.get("segments", {}),
        "char_count": exemplar.get("char_count", 0),
        "extracted_at": exemplar.get("extracted_at", ""),
        "saved_path": str(saved_path),
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def list_cmd(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import extract_exemplar  # type: ignore[import-not-found]
    import yaml  # bundled via wewrite deps

    index_file = extract_exemplar.INDEX_FILE
    if not index_file.exists():
        return {"total": 0, "exemplars": [], "by_category": {}, "note": "范文库为空, 先用 Extract 入库"}

    with open(index_file, "r", encoding="utf-8") as f:
        index: list[dict] = yaml.safe_load(f) or []

    filter_cat = bootstrap.pick(args, "category", "filter_category")
    if filter_cat:
        fc = str(filter_cat).strip().lower()
        if fc not in _VALID_CATEGORIES:
            # accept Chinese framework alias as filter too
            fc = _FRAMEWORK_TO_CATEGORY.get(fc, fc)
        index = [e for e in index if e.get("category") == fc]

    by_cat: dict[str, list] = {}
    for e in index:
        by_cat.setdefault(e.get("category", "general"), []).append(e)

    return {
        "total": len(index),
        "by_category": {k: len(v) for k, v in by_cat.items()},
        "exemplars": index,
    }


# ---------------------------------------------------------------------------
# Inject — the core integration point with Quill's writing loop
# ---------------------------------------------------------------------------

def _format_snippet(exemplars: list[dict], category: str, framework_hint: str = "") -> str:
    """Format selected exemplars into a prompt-ready text block.

    The result is meant to be spliced inline into Quill's writing prompt
    around the moment of step 3 (写作), giving the model concrete style
    references rather than just abstract rules.
    """
    if not exemplars:
        return ""

    label = framework_hint or category
    lines: list[str] = [
        f"以下是 {len(exemplars)} 篇高人类感的真实范文片段 (category={category}"
        + (f", framework={framework_hint}" if framework_hint else "")
        + "), 写作时**模仿其句长节奏、用词温度带、负面情绪比例、自纠插入语**, 但不要直接复制其内容:",
        "",
    ]

    for i, ex in enumerate(exemplars, 1):
        seg = ex.get("segments", {}) or {}
        meta_bits: list[str] = []
        if ex.get("source"):
            meta_bits.append(f"来源={ex['source']}")
        score = ex.get("humanness_score")
        if score is not None:
            meta_bits.append(f"humanness={score}/100")
        meta_line = " | ".join(meta_bits) if meta_bits else "(无元信息)"
        lines.append(f"--- 范文 {i} ({meta_line}) ---")

        if seg.get("opening"):
            lines.append(f"[开头钩子] {seg['opening']}")
        if seg.get("emotional_peak"):
            lines.append(f"[情绪高峰] {seg['emotional_peak']}")
        if seg.get("transition"):
            lines.append(f"[转折/自纠] {seg['transition']}")
        if seg.get("closing"):
            lines.append(f"[收尾] {seg['closing']}")
        lines.append("")

    lines.append(
        "重点对照: 上述范文里的短句插入、自我纠正、具体名词使用、副词节制 — "
        "这些是 TVSQuillWritingGuide.txt 反 AI 检测 14 条规则的实物范例。"
    )
    return "\n".join(lines)


def inject(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import extract_exemplar  # type: ignore[import-not-found]
    import yaml

    framework_raw = bootstrap.pick(args, "framework", "framework_type", "fw")
    category_raw = bootstrap.pick(args, "category")
    requested_category = _resolve_category(
        str(framework_raw) if framework_raw else None,
        str(category_raw) if category_raw else None,
    )

    top_n_raw = bootstrap.pick(args, "top_n", "n", "count", default=3)
    try:
        top_n = max(1, int(top_n_raw))
    except (TypeError, ValueError) as e:
        raise ValueError(f"top_n 必须是正整数, 收到 {top_n_raw!r}") from e

    max_humanness_raw = bootstrap.pick(args, "max_humanness", "humanness_ceiling", default=50)
    try:
        max_humanness = float(max_humanness_raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"max_humanness 必须是数字, 收到 {max_humanness_raw!r}") from e

    index_file = extract_exemplar.INDEX_FILE
    if not index_file.exists():
        return {
            "matched_category": requested_category or "(any)",
            "used_count": 0,
            "exemplars": [],
            "prompt_snippet": "",
            "note": "范文库为空, Quill 应使用默认写作规则 (TVSQuillWritingGuide) 而不依赖范文示范",
        }

    with open(index_file, "r", encoding="utf-8") as f:
        index: list[dict] = yaml.safe_load(f) or []

    # Filter by category if requested, else use all
    if requested_category:
        candidates = [e for e in index if e.get("category") == requested_category]
        fallback_used = False
        if not candidates:
            # Fallback: take any with low humanness; warn the agent in the note
            candidates = index
            fallback_used = True
    else:
        candidates = index
        fallback_used = False

    # Filter by max_humanness, sort ascending (lower = more human)
    filtered = [e for e in candidates if (e.get("humanness_score") or 100) <= max_humanness]
    if not filtered:
        # If nothing passes the ceiling, still take the best (lowest) available
        filtered = sorted(candidates, key=lambda e: e.get("humanness_score") or 100)[: top_n]
        ceiling_violated = True
    else:
        filtered = sorted(filtered, key=lambda e: e.get("humanness_score") or 100)[: top_n]
        ceiling_violated = False

    # Load full segments for each picked exemplar from the .md file frontmatter
    exemplars_dir = extract_exemplar.EXEMPLARS_DIR
    picked: list[dict] = []
    for entry in filtered:
        file_name = entry.get("file") or ""
        full_path = exemplars_dir / file_name
        seg: dict[str, str] = {}
        if full_path.exists():
            content = full_path.read_text(encoding="utf-8")
            # Strip frontmatter
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2]
            seg = _parse_segments(body)
        picked.append({
            "source": entry.get("source", ""),
            "category": entry.get("category", ""),
            "humanness_score": entry.get("humanness_score"),
            "extracted_at": entry.get("extracted_at", ""),
            "file": file_name,
            "segments": seg,
        })

    snippet = _format_snippet(
        picked,
        requested_category or "any",
        framework_hint=str(framework_raw or "").strip(),
    )

    notes: list[str] = []
    if fallback_used:
        notes.append(
            f"未找到 category={requested_category} 的范文, 已回退到所有 category 中按 humanness 排序"
        )
    if ceiling_violated:
        notes.append(
            f"范文库中没有 humanness_score ≤ {max_humanness} 的条目, 已用当前最高人类感范文兜底"
        )

    return {
        "matched_category": requested_category or "(any)",
        "framework": str(framework_raw or ""),
        "top_n_requested": top_n,
        "used_count": len(picked),
        "max_humanness_ceiling": max_humanness,
        "exemplars": picked,
        "prompt_snippet": snippet,
        "notes": notes,
    }


def _parse_segments(markdown_body: str) -> dict[str, str]:
    """Parse a saved exemplar .md body (after frontmatter strip) back into a
    {opening, emotional_peak, transition, closing} dict.

    extract_exemplar.save_exemplar writes sections like:
      ## 开头钩子\n\n<text>\n\n
      ## 情绪高峰\n\n<text>\n\n
      ## 转折/自纠\n\n<text>\n\n
      ## 收尾\n\n<text>\n\n
    """
    section_keymap = {
        "开头钩子": "opening",
        "情绪高峰": "emotional_peak",
        "转折/自纠": "transition",
        "收尾": "closing",
    }
    segments: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for line in markdown_body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            # Flush previous
            if current_key:
                segments[current_key] = "\n".join(current_lines).strip()
            heading = stripped[3:].strip()
            current_key = section_keymap.get(heading)
            current_lines = []
        else:
            if current_key:
                current_lines.append(line)
    if current_key:
        segments[current_key] = "\n".join(current_lines).strip()

    return {k: v for k, v in segments.items() if v}


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        args = bootstrap.read_args()
        cmd = str(bootstrap.pick(args, "command", "action", default="Extract")).strip().lower()
        if cmd in ("extract", "ingest", "add"):
            bootstrap.success(extract(args))
        elif cmd in ("list", "ls", "enumerate"):
            bootstrap.success(list_cmd(args))
        elif cmd in ("inject", "fewshot", "few_shot", "snippet"):
            bootstrap.success(inject(args))
        else:
            bootstrap.failure(
                f"未知命令 {cmd!r}。当前支持: Extract / List / Inject。",
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
            f"WeWriteExemplar 内部错误: {e}",
            code="INTERNAL_ERROR",
        )


if __name__ == "__main__":
    main()
