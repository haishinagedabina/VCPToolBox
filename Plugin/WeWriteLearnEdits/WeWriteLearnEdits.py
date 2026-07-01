"""WeWriteLearnEdits — playbook flywheel for Quill's writing-edit cycle.

Three commands:
  - Diff:          compare AI draft vs human-edited final, persist a lesson
                   file with empty `patterns: []` for the Agent to fill in
                   afterwards. Optionally auto-grows the exemplar library
                   if the final scores well.
  - RecordPattern: Agent writes typed patterns (word_sub / para_delete /
                   structure / ...) back into a lesson file.
  - Summarize:     aggregate all lessons by pattern key, compute confidence
                   from occurrences + recency, return sorted patterns for
                   the Agent to fold into playbook.md.

Implementation note: we directly import the upstream `learn_edits` and
`extract_exemplar` modules (both pure stdlib + yaml + difflib) instead
of spawning subprocesses. This lets us tightly control the lesson
file lifecycle and avoid PowerShell stdio encoding quirks observed
during Phase 1.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402

_VALID_TYPES = {
    "word_sub", "para_delete", "para_add",
    "structure", "title", "tone", "expression",
}


def _load_md(args: dict, key_prefixes: tuple[str, ...], param_label: str) -> tuple[str, str]:
    """Resolve {prefix}_content or {prefix}_path into (text, source_label).

    source_label is either the path string or '<inline>' to record provenance
    in the lesson YAML.
    """
    for k in key_prefixes:
        content = bootstrap.pick(args, f"{k}_content", f"{k}_text", f"{k}_markdown", f"{k}_body")
        if content:
            return str(content), "<inline>"
    for k in key_prefixes:
        file_arg = bootstrap.pick(args, f"{k}_path", f"{k}_file", f"{k}")
        if file_arg:
            md_path = bootstrap.materialize_markdown(str(file_arg))
            return md_path.read_text(encoding="utf-8"), str(md_path)
    raise ValueError(
        f"必需参数缺失: {param_label} 需要提供 `{key_prefixes[0]}_content` (字符串) 或 "
        f"`{key_prefixes[0]}_path` (文件路径) 之一。"
    )


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import learn_edits  # type: ignore[import-not-found]

    draft, draft_label = _load_md(args, ("draft", "ai", "before"), "draft (AI 初稿)")
    final, final_label = _load_md(args, ("final", "edited", "after", "human"), "final (人工终稿)")

    # Pass raw markdown to compute_diff so that extract_title (looks for
    # leading "# ") and split_sections (looks for "## ") can detect title
    # changes and structure changes. Upstream's plaintext-strip path is
    # only used when comparing against WeChat draft box returns (which
    # don't have markdown markers).
    diff_result = learn_edits.compute_diff(draft, final)
    lesson_file = learn_edits.save_lesson(diff_result, draft_label, final_label)

    payload: dict[str, Any] = {
        "lesson_file": str(lesson_file),
        "draft_source": draft_label,
        "final_source": final_label,
        "diff_summary": {
            "title_changed": diff_result["title_changed"],
            "draft_title": diff_result["draft_title"],
            "final_title": diff_result["final_title"],
            "structure_changed": diff_result["structure_changed"],
            "draft_h2s": diff_result["draft_h2s"],
            "final_h2s": diff_result["final_h2s"],
            "lines_added": diff_result["lines_added"],
            "lines_deleted": diff_result["lines_deleted"],
            "draft_chars": diff_result["draft_chars"],
            "final_chars": diff_result["final_chars"],
            "char_diff": diff_result["char_diff"],
        },
        "additions_sample": diff_result["additions_sample"],
        "deletions_sample": diff_result["deletions_sample"],
        "next_step": (
            "Agent 现在应该对照 additions_sample 和 deletions_sample 分析修改模式, "
            f"用 RecordPattern 命令把 typed patterns 写回 {lesson_file}。"
            "patterns 必须包含 type/key/description/rule 四个字段, rule 写成可执行祈使句。"
        ),
    }

    # Optionally auto-grow the exemplar library if final scores well
    auto_extract = bootstrap.pick(args, "auto_extract_exemplar", "auto_exemplar", default=True)
    if str(auto_extract).lower() not in ("0", "false", "no", "off"):
        try:
            import extract_exemplar  # type: ignore[import-not-found]
            final_title = learn_edits.extract_title(final) or "user-edited"
            exemplar = extract_exemplar.extract_exemplar(final, source=final_title)
            if exemplar.get("humanness_score", 100) <= 50:
                exemplar_path = extract_exemplar.save_exemplar(exemplar)
                payload["extracted_to_exemplar"] = {
                    "saved_path": str(exemplar_path),
                    "category": exemplar["category"],
                    "humanness_score": exemplar["humanness_score"],
                }
            else:
                payload["exemplar_skipped"] = {
                    "reason": "humanness_score > 50, not human-like enough",
                    "humanness_score": exemplar.get("humanness_score"),
                }
        except Exception as e:  # noqa: BLE001
            payload["exemplar_error"] = str(e)

    return payload


# ---------------------------------------------------------------------------
# RecordPattern
# ---------------------------------------------------------------------------

def _validate_patterns(raw: Any) -> list[dict]:
    if raw is None or raw == "":
        raise ValueError("patterns 参数必填: 至少一条 {type, key, description, rule}")
    if isinstance(raw, str):
        import json as _json
        try:
            raw = _json.loads(raw)
        except _json.JSONDecodeError as e:
            raise ValueError(f"patterns 不是合法 JSON: {e}") from e
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ValueError("patterns 必须是非空数组")

    validated: list[dict] = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError(f"patterns[{i}] 不是 object: {p!r}")
        ptype = str(p.get("type", "")).strip()
        if ptype not in _VALID_TYPES:
            raise ValueError(
                f"patterns[{i}].type={ptype!r} 不在允许集合, 候选: {sorted(_VALID_TYPES)}"
            )
        key = str(p.get("key", "")).strip()
        if not key:
            raise ValueError(f"patterns[{i}].key 必填 (snake_case 短标识)")
        rule = str(p.get("rule", "")).strip()
        if not rule:
            raise ValueError(f"patterns[{i}].rule 必填 (写成可执行祈使句, 不是描述句)")
        description = str(p.get("description", "")).strip()
        validated.append({
            "type": ptype,
            "key": key,
            "description": description,
            "rule": rule,
        })
    return validated


def record_pattern(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import yaml

    patterns = _validate_patterns(bootstrap.pick(args, "patterns", "pattern", "rules"))

    lessons_dir = bootstrap.SKILL_DIR / "lessons"
    if not lessons_dir.exists():
        raise ValueError(
            "lessons/ 目录不存在: 请先用 Diff 命令生成 lesson 文件, 再 RecordPattern。"
        )

    lesson_file_arg = bootstrap.pick(args, "lesson_file", "lesson_path", "lesson")
    if lesson_file_arg:
        target = Path(str(lesson_file_arg))
        if not target.is_absolute():
            target = lessons_dir / target.name
        if not target.exists():
            raise FileNotFoundError(f"lesson_file 不存在: {lesson_file_arg}")
    else:
        candidates = sorted(lessons_dir.glob("*-diff*.yaml"))
        if not candidates:
            raise ValueError(
                "lessons/ 下没有 *-diff*.yaml 文件; 请先用 Diff 命令生成。"
            )
        # Use the most recently modified lesson
        target = max(candidates, key=lambda p: p.stat().st_mtime)

    with open(target, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    merge = bootstrap.pick(args, "merge", "append", default=False)
    merge_flag = str(merge).lower() in ("1", "true", "yes", "on")

    existing = data.get("patterns") or []
    if merge_flag and isinstance(existing, list):
        # Drop existing patterns whose key collides, then append new
        new_keys = {p["key"] for p in patterns}
        kept = [p for p in existing if p.get("key") not in new_keys]
        merged = kept + patterns
    else:
        merged = patterns

    data["patterns"] = merged
    data["last_pattern_update"] = datetime.now().isoformat()

    with open(target, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    return {
        "lesson_file": str(target),
        "patterns_count": len(merged),
        "newly_recorded": len(patterns),
        "merge_mode": merge_flag,
    }


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

_PATTERN_TYPE_ZH = {
    "word_sub": "用词替换",
    "para_delete": "段落删除",
    "para_add": "段落新增",
    "structure": "结构调整",
    "title": "标题修改",
    "tone": "语气调整",
    "expression": "表达偏好",
}


def summarize(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()
    import learn_edits  # type: ignore[import-not-found]

    lessons = learn_edits.load_all_lessons()
    if not lessons:
        return {
            "total_lessons": 0,
            "total_patterns": 0,
            "patterns": [],
            "note": "lessons/ 下没有任何记录, 先跑 Diff + RecordPattern 攒数据",
        }

    min_conf_raw = bootstrap.pick(args, "min_confidence", "min_conf", default=0)
    try:
        min_conf = float(min_conf_raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"min_confidence 必须是数字, 收到 {min_conf_raw!r}") from e

    patterns_all = learn_edits.aggregate_patterns(lessons)
    patterns = [p for p in patterns_all if p.get("confidence", 0) >= min_conf]

    fmt = str(bootstrap.pick(args, "format", "mode", default="summary")).strip().lower()
    is_json = fmt in ("json", "raw", "full")

    result: dict[str, Any] = {
        "total_lessons": len(lessons),
        "total_patterns": len(patterns_all),
        "filtered_patterns": len(patterns),
        "min_confidence": min_conf,
        "patterns": patterns,
    }

    if not is_json:
        # Add human-readable type labels and group by type for the agent's
        # convenience when writing a playbook.md
        by_type: dict[str, list] = {}
        for p in patterns:
            p_copy = dict(p)
            p_copy["type_label"] = _PATTERN_TYPE_ZH.get(p["type"], p["type"])
            by_type.setdefault(p["type"], []).append(p_copy)
        result["by_type"] = {k: sorted(v, key=lambda x: -x["confidence"]) for k, v in by_type.items()}
        # High-confidence shortcuts
        result["high_confidence"] = [p for p in patterns if p.get("confidence", 0) >= 7]
        result["medium_confidence"] = [
            p for p in patterns if 4 <= p.get("confidence", 0) < 7
        ]

    # Playbook nudge: every 5 lessons, suggest a playbook update
    if len(lessons) >= 5 and len(lessons) % 5 == 0:
        result["playbook_suggestion"] = (
            f"已积累 {len(lessons)} 条改稿记录, 是时候更新 wewrite/playbook.md。"
            "建议从 high_confidence (≥7) 列表里挑出新规则, 写成祈使句加进 playbook。"
        )

    return result


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        args = bootstrap.read_args()
        cmd = str(bootstrap.pick(args, "command", "action", default="Diff")).strip().lower()
        if cmd in ("diff", "compare"):
            bootstrap.success(diff(args))
        elif cmd in ("recordpattern", "record_pattern", "record", "addpattern", "add_pattern"):
            bootstrap.success(record_pattern(args))
        elif cmd in ("summarize", "summary", "aggregate", "report"):
            bootstrap.success(summarize(args))
        else:
            bootstrap.failure(
                f"未知命令 {cmd!r}。当前支持: Diff / RecordPattern / Summarize。",
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
            f"WeWriteLearnEdits 内部错误: {e}",
            code="INTERNAL_ERROR",
        )


if __name__ == "__main__":
    main()
