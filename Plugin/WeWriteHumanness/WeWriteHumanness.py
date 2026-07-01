"""WeWriteHumanness — AI-trace scoring for Quill's articles.

Thin VCP wrapper around wewrite/scripts/humanness_score.py. We directly
import `score_article` (pure stdlib + re) instead of spawning a subprocess
because Quill calls this on every article write — keeping it in-process
costs ~50ms vs ~500ms per subprocess on Windows.

Default `summary` mode shrinks the verbose JSON into a verdict + the 5
weakest dimensions + actionable tips that map back to TVSQuillWritingGuide
rule IDs. `json` mode returns the full original payload for power users.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_CORE_DIR = Path(__file__).resolve().parent.parent / "WeWriteCore"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import bootstrap  # noqa: E402


# Improvement tips keyed by check name. Each tip is a single concrete action
# the agent can apply during rewriting — phrased to mirror the wording used
# in TVStxt/QuillWritingGuide.txt so the agent can cross-reference.
_TIPS: dict[str, str] = {
    "sentence_length_stddev": "[1.1] 句长方差不足: 加入 1-5 字短句 (如 '嗯。' / '不对。' / '两年, 10 倍。'), 紧邻 40+ 字长句出现, 制造落差",
    "sentence_length_range": "[1.1] 句长跨度太窄: 全文最短和最长句应相差 ≥ 30 字; 每 500 字至少 1 个 1-5 字短段独立成段",
    "paragraph_length_variance": "[1.3] 段落节奏太均匀: 避免连续 2 段长度相近 (±20 字), 长段间插 1 句话短段",
    "vocabulary_richness": "[1.2] 词汇丰富度不足: 在同一段内混搭冷词 (边际/认知负荷) / 温词 (说白了/其实吧) / 热词 (DNA动了/卷) / 野词 (整挺好/瞎折腾)",
    "negative_emotion_ratio": "[1.4] 负面情绪太少: 加入 '吐槽 / 质疑 / 不满 / 焦虑' 类表达, 占比应 ≥ 20% (人类基线 25-34%, AI 仅 11-12%)",
    "adverb_density": "[1.5] 副词过多: 用具体描述替代 ('非常快速地增长' → '三个月翻了一番'); 避免连续两句都以副词开头",
    "banned_words": "[2.1] 命中 AI 套话黑名单: 全文搜索 '首先/其次/总而言之/此外/与此同时/不言而喻/众所周知/事实上/可以说' 并删除替换",
    "broken_sentences": "[2.2] 缺少破句结构: 加入 '——算了, 你自己品。' / 自我纠正 / 括号插入语 / 反问连击 / 语气词断句, 每 500 字至少 3 处",
    "real_sources": "[3.1] 真实信源不足: 每个 H2 至少 1 条具名来源 (人名/百分比/年份/具体金额), 用 {{VCPVSearch}} 或 {{VCPFlashDeepSearch}} 补充",
    "word_temperature_mix": "[1.2] 词汇温度覆盖不足: 全文应混用 ≥ 3 种温度带 (冷/温/热/野), 不要单一温度铺到底",
    "self_correction": "[2.2] 缺少自我纠正: 加入 '不对, 准确说...' / '我记混了' / '其实不是' / '更准确地说', 或长括号插入语 (≥ 4 字)",
}


_VERDICTS = [
    (30.0, "✅ 人类感很强"),
    (50.0, "⚠️ 偏 AI 但仍可接受"),
    (70.0, "❌ AI 味较重, 建议改稿"),
    (101.0, "🚨 显著 AI 痕迹, 必须改稿"),
]


def _verdict_for(composite: float) -> str:
    for threshold, label in _VERDICTS:
        if composite < threshold:
            return f"{label} (composite={composite:.1f}/100)"
    return f"🚨 显著 AI 痕迹 (composite={composite:.1f}/100)"


def _load_content(args: dict) -> str:
    content = bootstrap.pick(args, "content", "text", "markdown", "article", "body")
    if content:
        return str(content)
    file_arg = bootstrap.pick(args, "file_path", "path", "input", "file")
    if file_arg:
        md_path = bootstrap.materialize_markdown(str(file_arg))
        return md_path.read_text(encoding="utf-8")
    raise ValueError(
        "必需参数缺失: 请传 `content` (文章正文字符串) 或 `file_path` (文件路径)。"
    )


def _summarize(full: dict[str, Any]) -> dict[str, Any]:
    """Shrink the upstream verbose JSON into an agent-friendly digest."""
    composite = float(full["composite_score"])

    issues: list[dict[str, Any]] = []
    for tier_name in ("tier1", "tier2"):
        tier = full.get(tier_name, {}) or {}
        for check_name, data in tier.items():
            if check_name.startswith("_") or not isinstance(data, dict):
                continue
            issues.append({
                "check": check_name,
                "tier": tier_name,
                "score": float(data.get("score", 0.0)),
                "detail": str(data.get("detail", "")),
                "param": data.get("param"),
            })

    issues.sort(key=lambda x: x["score"])

    weak: list[dict[str, Any]] = []
    for issue in issues:
        if issue["score"] >= 0.8 and len(weak) >= 3:
            break
        if len(weak) >= 5:
            break
        weak.append({
            "check": issue["check"],
            "tier": issue["tier"],
            "score": round(issue["score"], 2),
            "detail": issue["detail"],
            "tip": _TIPS.get(issue["check"], f"参考 TVSQuillWritingGuide 中 `{issue['check']}` 对应规则"),
        })

    tier1_sum = (full.get("tier1") or {}).get("_summary", {}) or {}
    tier2_sum = (full.get("tier2") or {}).get("_summary", {}) or {}
    tier3 = full.get("tier3") or {}

    summary: dict[str, Any] = {
        "composite_score": composite,
        "verdict": _verdict_for(composite),
        "char_count": full.get("char_count", 0),
        "tier_scores": {
            "tier1_statistical": tier1_sum.get("mean_score"),
            "tier2_pattern": tier2_sum.get("mean_score"),
            "tier3_llm": tier3.get("score"),
        },
        "weights": full.get("weights"),
        "over_optimization_penalty": full.get("over_optimization_penalty", 1.0),
        "weakest_dimensions": weak,
        "param_scores": full.get("param_scores", {}),
    }

    # If the article scored >= 70 add a top-level rewrite_required flag so the
    # agent's control flow can branch on a single boolean.
    summary["rewrite_required"] = composite >= 70.0
    summary["rewrite_recommended"] = composite >= 50.0

    return summary


def _resolve_tier3(args: dict) -> float | None:
    raw = bootstrap.pick(args, "tier3_score", "tier3", "llm_score", "agent_score")
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"tier3_score 无法解析为浮点: {raw!r}") from e
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"tier3_score 必须在 [0, 1] 区间, 收到 {v}")
    return v


def score(args: dict) -> dict[str, Any]:
    bootstrap.ensure_runtime()
    bootstrap.inject_toolkit_path()  # also adds SCRIPTS_DIR

    from humanness_score import score_article  # type: ignore[import-not-found]

    text = _load_content(args)
    if not text.strip():
        raise ValueError("文章内容为空, 无法评分")

    tier3 = _resolve_tier3(args)
    full = score_article(text, verbose=False, tier3_score=tier3)

    mode = str(bootstrap.pick(args, "mode", "format", default="summary")).strip().lower()
    if mode in ("json", "full", "raw", "verbose"):
        return full
    return _summarize(full)


def main() -> None:
    try:
        args = bootstrap.read_args()
        cmd = str(bootstrap.pick(args, "command", "action", default="Score")).strip().lower()
        if cmd in ("", "score", "evaluate", "check", "humanness"):
            bootstrap.success(score(args))
        else:
            bootstrap.failure(
                f"未知命令 {cmd!r}。当前仅支持: Score。",
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
            f"WeWriteHumanness 内部错误: {e}",
            code="INTERNAL_ERROR",
        )


if __name__ == "__main__":
    main()
