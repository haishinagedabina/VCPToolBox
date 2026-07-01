#!/usr/bin/env python3
"""
Humanness scoring for WeWrite articles.

Three-tier evaluation aligned with writing-guide.md's anti-AI checklist:

  Tier 1 (Statistical, 50%): 6 checks measuring statistical properties
         that AI detectors analyze (burstiness, distribution, variance).
  Tier 2 (Pattern, 30%):     5 checks for specific linguistic patterns
         (banned words, broken sentences, real sources).
  Tier 3 (LLM, 20%):        Semantic analysis done by the agent in SKILL.md
         (style drift, density waves, coherence). Passed via --tier3 flag.

Each check outputs a continuous 0-1 score and maps to a writing-config
parameter, so the optimization loop knows which knob to turn.

Standalone mode (no --tier3): weights redistribute to T1=62.5%, T2=37.5%.

Usage:
    python3 humanness_score.py article.md                    # single score
    python3 humanness_score.py article.md --verbose          # detailed report
    python3 humanness_score.py article.md --json             # full JSON
    python3 humanness_score.py article.md --json --tier3 0.7 # with agent score
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ============================================================
# Constants
# ============================================================

BANNED_WORDS = [
    "首先", "其次", "再者", "最后", "总之", "综上所述", "总而言之",
    "此外", "另外", "与此同时", "不仅如此", "更重要的是", "在此基础上",
    "作为一个", "让我们", "值得注意的是", "需要指出的是", "不可否认",
    "毋庸置疑", "众所周知", "事实上", "显而易见", "可以说", "从某种意义上说",
    "非常重要", "至关重要", "不言而喻", "具有重要意义", "发挥着重要作用",
    "意义深远", "影响深远", "引发了广泛关注", "引起了热烈讨论",
    "总的来说", "综合来看", "由此可见", "不难发现", "通过以上分析",
    "正如我们所看到的",
]

REAL_SOURCE_PATTERNS = [
    r'[A-Z][a-z]+\s+[A-Z][a-z]+',
    r'[\u4e00-\u9fff]{2,4}(?:表示|指出|认为|写道|提到|说过)',
    r'(?:据|根据|来自)\s*[\u4e00-\u9fff]+(?:报告|数据|研究|调查)',
    r'20[12]\d\s*年',
    r'\d+(?:\.\d+)?%',
    r'(?:亿|万)\s*(?:美元|元|人民币)',
]

NEGATIVE_MARKERS = [
    # 直接负面情绪
    "失望", "糟糕", "扯", "坑", "烂", "差劲", "崩溃", "吐槽", "骂",
    "怒", "烦", "焦虑", "担忧", "不满", "恶心", "可怕", "可悲", "可笑",
    "离谱", "尴尬", "无语", "蠢", "惨", "亏", "危",
    # 绝望/迷茫
    "绝望", "迷茫", "心累", "丧", "后悔", "后怕", "心寒",
    # 欺骗/操控（隐性负面）
    "骗", "忽悠", "割韭菜", "套路", "画大饼", "洗脑",
    # 失败/徒劳
    "白费", "白搭", "没戏", "黄了", "凉了", "废了",
    # 自嘲/自贬
    "傻", "天真", "吃亏", "自嗨", "打脸",
    # 讽刺/反语
    "呵呵", "好吧", "行吧", "真服了",
    # 短语
    "太扯了", "说实话我很失望", "搞什么", "不靠谱", "受不了",
    "受够了", "想哭", "伤心", "苦哈哈", "得过且过",
]

COMMON_ADVERBS = [
    "非常", "十分", "极其", "特别", "相当", "尤其", "格外",
    "更加", "越来越", "逐渐", "不断", "始终", "一直",
    "已经", "正在", "将要", "可能", "大概", "或许",
    "似乎", "显然", "明显", "确实", "果然", "居然",
    "竟然", "简直", "几乎", "完全", "绝对", "必然",
]

COLD_WORDS = [
    "边际", "认知负荷", "信息不对称", "路径依赖", "商业模式", "生态系统", "增量",
    "技术栈", "标准化", "结构性", "规模化", "护城河", "飞轮", "闭环",
    "赛道", "壁垒", "方法论", "底层逻辑", "第一性原理", "杠杆", "复利",
    "ROI", "PMF", "代运营", "供给侧", "需求侧",
]
WARM_WORDS = [
    "说白了", "其实吧", "讲真", "说实话", "坦白讲", "懂的都懂", "怎么说呢",
    "老实说", "这么说吧", "你想啊", "别急", "慢慢来",
    "有意思的是", "好玩的是", "巧的是", "说来话长", "话说回来",
]
HOT_WORDS = [
    "DNA动了", "格局打开", "遥遥领先", "卷", "内卷", "炸了", "杀疯了", "吃灰",
    "凡尔赛", "标题党", "躺平", "摆烂", "破防", "上头", "内耗",
    "蒸发", "出圈", "降维打击", "弯道超车",
]
WILD_WORDS = [
    "整挺好", "不靠谱", "瞎折腾", "搁这儿", "糊弄", "扯", "嗯",
    "苦哈哈", "傻乎乎", "稀里糊涂", "得了吧", "算了吧",
    "摔了跤", "交学费", "踩坑", "翻车", "栽了",
]

SELF_CORRECTION_PATTERNS = [
    r'不对[，,]', r'准确说', r'算了', r'说错了',
    r'其实不是', r'我记混了', r'应该说', r'更准确地说',
    r'（[^）]{4,}）',  # Chinese parenthetical insertion (≥4 chars)
]

BROKEN_SENTENCE_PATTERNS = [
    r'——(?!.*[，。！？])',
    r'\.{3,}|…',
    r'不对[，,]',
    r'算了',
]


# ============================================================
# Helpers
# ============================================================

def _split_sentences(text):
    """Split text by Chinese sentence-ending punctuation."""
    sentences = re.split(r'[。！？\n]', text)
    return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 1]


def _split_paragraphs(text):
    """Split text into paragraphs, excluding headings."""
    return [p.strip() for p in text.split('\n\n')
            if p.strip() and not p.strip().startswith('#')]


def _make_result(score, detail, param=None):
    """Create a check result dict."""
    r = {"score": round(max(0.0, min(1.0, score)), 4), "detail": detail}
    if param is not None:
        r["param"] = param
    else:
        r["param"] = None
    return r


# ============================================================
# Tier 1: Statistical Checks (weight 50%)
# ============================================================

def score_sentence_length_stddev(text):
    """[1.1] Sentence length standard deviation. → sentence_variance"""
    sentences = _split_sentences(text)
    if len(sentences) < 5:
        return _make_result(0.5, "too few sentences to measure", "sentence_variance")
    lengths = [len(s) for s in sentences]
    mean = sum(lengths) / len(lengths)
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    stddev = variance ** 0.5
    score = min(1.0, stddev / 25.0)
    return _make_result(score, f"stddev={stddev:.1f} (target ≥15)", "sentence_variance")


def score_sentence_length_range(text):
    """[1.1] Sentence length range (max - min). → sentence_variance"""
    sentences = _split_sentences(text)
    if len(sentences) < 5:
        return _make_result(0.5, "too few sentences", "sentence_variance")
    lengths = [len(s) for s in sentences]
    rng = max(lengths) - min(lengths)
    range_score = min(1.0, rng / 40.0)
    # Check for single-sentence short paragraphs
    lines = text.split('\n')
    short_paras = sum(1 for l in lines if l.strip() and 1 <= len(l.strip()) <= 5
                      and not l.strip().startswith('#'))
    expected = max(1, len(text) / 500)
    short_score = min(1.0, short_paras / expected)
    score = range_score * 0.6 + short_score * 0.4
    return _make_result(score, f"range={rng} (target ≥30), short_paras={short_paras}", "sentence_variance")


def score_paragraph_length_variance(text):
    """[1.3] Paragraph length variance. → paragraph_rhythm"""
    paragraphs = _split_paragraphs(text)
    if len(paragraphs) < 3:
        return _make_result(0.5, "too few paragraphs", "paragraph_rhythm")
    total_pairs = len(paragraphs) - 1
    similar = sum(1 for i in range(total_pairs)
                  if abs(len(paragraphs[i]) - len(paragraphs[i + 1])) <= 20)
    score = 1.0 - (similar / total_pairs) if total_pairs > 0 else 0.5
    return _make_result(score, f"{similar}/{total_pairs} consecutive similar-length pairs", "paragraph_rhythm")


def score_vocabulary_richness(text):
    """[1.2] CJK bigram type-token ratio + temperature mix. → word_temperature_bias"""
    cjk_chars = re.findall(r'[\u4e00-\u9fff]', text)
    if len(cjk_chars) < 20:
        return _make_result(0.5, "too few CJK characters", "word_temperature_bias")
    bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    ttr = len(set(bigrams)) / len(bigrams) if bigrams else 0
    ttr_score = min(1.0, ttr / 0.7)
    # Temperature mix bonus
    found_temps = sum([
        any(w in text for w in COLD_WORDS),
        any(w in text for w in WARM_WORDS),
        any(w in text for w in HOT_WORDS),
        any(w in text for w in WILD_WORDS),
    ])
    temp_bonus = found_temps / 4.0 * 0.3
    score = min(1.0, ttr_score * 0.7 + temp_bonus)
    return _make_result(score, f"bigram_ttr={ttr:.3f}, temps={found_temps}/4", "word_temperature_bias")


def score_negative_emotion_ratio(text):
    """[1.4] Negative emotion ratio. → emotional_arc"""
    sentences = _split_sentences(text)
    if not sentences:
        return _make_result(0.5, "no sentences", "emotional_arc")
    negative_count = sum(1 for s in sentences
                         if any(m in s for m in NEGATIVE_MARKERS))
    ratio = negative_count / len(sentences)
    score = min(1.0, ratio / 0.25)
    return _make_result(score, f"negative={negative_count}/{len(sentences)} ({ratio:.0%}, target ≥20%)", "emotional_arc")


def score_adverb_density(text):
    """[1.5] Adverb density control. → adverb_max_per_100"""
    char_count = len(text)
    if char_count < 50:
        return _make_result(0.5, "text too short", "adverb_max_per_100")
    # Count adverb occurrences
    total_adverbs = sum(text.count(adv) for adv in COMMON_ADVERBS)
    density = total_adverbs / char_count * 100
    # Check consecutive sentences starting with adverbs
    sentences = _split_sentences(text)
    consecutive_adverb_starts = 0
    for i in range(len(sentences) - 1):
        a_starts = any(sentences[i].startswith(adv) for adv in COMMON_ADVERBS)
        b_starts = any(sentences[i + 1].startswith(adv) for adv in COMMON_ADVERBS)
        if a_starts and b_starts:
            consecutive_adverb_starts += 1
    score = 1.0
    if density > 3.0:
        score -= min(0.5, (density - 3.0) * 0.1)
    score -= consecutive_adverb_starts * 0.3
    return _make_result(score, f"density={density:.1f}/100chars, consecutive_starts={consecutive_adverb_starts}", "adverb_max_per_100")


# ============================================================
# Tier 2: Pattern Checks (weight 30%)
# ============================================================

def score_banned_words(text):
    """[2.1] Banned word check. → null (hard rule, no config param)"""
    found = [w for w in BANNED_WORDS if w in text]
    score = max(0.0, 1.0 - len(found) * 0.2)
    detail = "0 banned words" if not found else f"{len(found)} found: {found[:5]}"
    return _make_result(score, detail, None)


def score_broken_sentences(text):
    """[2.2] Broken/incomplete sentence patterns. → broken_sentence_rate"""
    count = 0
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for p in BROKEN_SENTENCE_PATTERNS:
            count += len(re.findall(p, line))
        if 1 <= len(line) <= 10 and not line.startswith('#'):
            count += 1
    char_count = len(text)
    expected = max(3, char_count / 500 * 3)
    score = min(1.0, count / expected)
    return _make_result(score, f"{count} broken structures (expected ≥{expected:.0f})", "broken_sentence_rate")


def score_real_sources(text):
    """[3.1] Real external source indicators. → real_data_density"""
    count = 0
    for pattern in REAL_SOURCE_PATTERNS:
        count += len(re.findall(pattern, text))
    score = min(1.0, count / 5.0)
    return _make_result(score, f"{count} real-source indicators (target ≥5)", "real_data_density")


def score_word_temperature_mix(text):
    """[1.2] Word temperature band coverage. → word_temperature_bias"""
    found_temps = sum([
        any(w in text for w in COLD_WORDS),
        any(w in text for w in WARM_WORDS),
        any(w in text for w in HOT_WORDS),
        any(w in text for w in WILD_WORDS),
    ])
    score = max(0.0, (found_temps - 1) / 3.0)
    return _make_result(score, f"{found_temps}/4 temperature bands", "word_temperature_bias")


def score_self_correction(text):
    """[2.2] Self-correction and parenthetical patterns. → self_correction_rate"""
    count = 0
    for pattern in SELF_CORRECTION_PATTERNS:
        count += len(re.findall(pattern, text))
    score = min(1.0, count / 3.0)
    return _make_result(score, f"{count} self-corrections/insertions (target ≥3)", "self_correction_rate")


# ============================================================
# Tier Runners
# ============================================================

TIER1_CHECKS = [
    ("sentence_length_stddev", score_sentence_length_stddev),
    ("sentence_length_range", score_sentence_length_range),
    ("paragraph_length_variance", score_paragraph_length_variance),
    ("vocabulary_richness", score_vocabulary_richness),
    ("negative_emotion_ratio", score_negative_emotion_ratio),
    ("adverb_density", score_adverb_density),
]

TIER2_CHECKS = [
    ("banned_words", score_banned_words),
    ("broken_sentences", score_broken_sentences),
    ("real_sources", score_real_sources),
    ("word_temperature_mix", score_word_temperature_mix),
    ("self_correction", score_self_correction),
]

# Tech article checks — measure authenticity signals that replace
# the generic anti-AI markers (emotion, temperature, etc.) which
# are not appropriate for technical tutorials.
TECH_CHECKS = [
    ("version_references", "score_version_references"),
    ("code_block_density", "score_code_block_density"),
    ("honesty_markers", "score_honesty_markers"),
    ("concrete_measurements", "score_concrete_measurements"),
    ("pitfall_records", "score_pitfall_records"),
]

# Generic checks to SKIP (run but zero-weight) or REDUCE weight
# for tech articles — these dimensions penalise legitimate
# technical-writing patterns.
TECH_SKIP_CHECKS = {
    "negative_emotion_ratio",   # tech tutorials aren't supposed to be angry
    "word_temperature_mix",     # high jargon density is correct for tech
    "vocabulary_richness",      # bigram TTR + temp mix not applicable
}


# ============================================================
# Tech-specific check functions
# ============================================================

def score_version_references(text):
    """Count version numbers, API names, package references. → None (tech-only)"""
    patterns = [
        r'\b\d+\.\d+(?:\.\d+)?(?:[a-z]+\d*)?\b',  # semver: 0.10.25, 3.11
        r'\bpip\s+(?:install|uninstall)\s+\S+',     # pip install xxx
        r'\bnpm\s+(?:install|i)\s+\S+',              # npm install xxx
        r'\b(?:import|from)\s+\S+',                  # Python imports
        r'\b(?:require|include)\s*\(?\s*[\'"]',      # JS require/include
        r'[A-Z][a-z]+API\b',                         # FooAPI
        r'\b(?:OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|SECRET)\b',  # env var refs
    ]
    count = sum(len(re.findall(p, text)) for p in patterns)
    score = min(1.0, count / 5.0)
    return _make_result(score, f"{count} version/API references (target ≥5)", None)


def score_code_block_density(text):
    """Check code blocks exist and have language tags. → None (tech-only)"""
    code_blocks = re.findall(r'```(\w*)', text)
    total = len(code_blocks)
    tagged = sum(1 for lang in code_blocks if lang)
    char_count = len(text)
    # Expect code to be 15-40% of a good tech article
    code_chars = sum(len(b) for b in re.findall(r'```.*?```', text, re.DOTALL))
    code_ratio = code_chars / max(1, char_count)
    ratio_score = min(1.0, code_ratio / 0.15)
    tag_score = min(1.0, tagged / max(1, total)) if total > 0 else 0.0
    score = ratio_score * 0.6 + tag_score * 0.4 if total > 0 else 0.0
    return _make_result(score,
                        f"{total} code blocks ({tagged} tagged), code_ratio={code_ratio:.0%}",
                        None)


def score_honesty_markers(text):
    """Count limitation/uncertainty/honesty signals. → None (tech-only)"""
    patterns = [
        r'(?:局限|问题|缺点|不足).{0,20}(?:是|在于|在于：)',
        r'(?:不适用|不适合|不能用于)',
        r'(?:我还没|还没找到|暂时没有|尚不支持)',
        r'(?:注意|⚠️|小心|谨慎).{0,30}(?:不要|别|避免|会)',
        r'(?:超过|大于|多于)\s*\d+\s*(?:页|条|个|GB|MB).{0,20}(?:会|将|就)',
        r'(?:也可以|另一个选择|替代方案|或者)',
    ]
    count = sum(len(re.findall(p, text)) for p in patterns)
    score = min(1.0, count / 2.0)
    return _make_result(score, f"{count} honesty/limitation markers (target ≥2)", None)


def score_concrete_measurements(text):
    """Count specific numbers, timings, measurements. → None (tech-only)"""
    patterns = [
        r'(?:约|大概|~|≈)\s*\d+\s*(?:ms|s|sec|分钟|秒|MB|GB|KB)',
        r'\d+\s*(?:ms|毫秒|秒)\b',
        r'(?:延迟|耗时|花费|用了)\s*.{0,10}\d+\s*(?:ms|秒|分钟)',
        r'(?:QPS|TPS|RPS|吞吐).{0,10}\d+',
        r'\d+\s*(?:倍|%|％|percent)',
    ]
    count = sum(len(re.findall(p, text)) for p in patterns)
    score = min(1.0, count / 2.0)
    return _make_result(score, f"{count} concrete measurements (target ≥2)", None)


def score_pitfall_records(text):
    """Count debugging / error / gotcha narratives. → None (tech-only)"""
    patterns = [
        r'(?:报错|报了一|错误信息|error|exception|traceback)',
        r'(?:踩坑|坑|翻车|踩了|花了我.*小时)',
        r'(?:忘了|忘记|没注意到|忽略了一)',
        r'(?:第一[次遍].{0,10}跑|第一次.{0,10}时候)',
        r'(?:调试|debug|排查|找了半天)',
        r'(?:最后发现|原来是|结果是|因为.{0,20}导致)',
    ]
    count = sum(len(re.findall(p, text, re.IGNORECASE)) for p in patterns)
    score = min(1.0, count / 2.0)
    return _make_result(score, f"{count} pitfall/debugging records (target ≥2)", None)


def run_tier(checks, text):
    """Run a tier of checks. Returns dict keyed by check name + _summary."""
    results = {}
    scores = []
    for name, fn in checks:
        r = fn(text)
        results[name] = r
        scores.append(r["score"])
    results["_summary"] = {
        "count": len(checks),
        "mean_score": round(sum(scores) / len(scores), 4) if scores else 0,
        "scores": [round(s, 4) for s in scores],
    }
    return results


# ============================================================
# Calibration (bell-curve + over-optimization penalty)
# ============================================================

# Human article baselines (from 15 example articles, 2026-03-30)
# Dimensions where AI over-optimizes: bell-curve scoring penalizes
# both "too low" AND "too high" relative to human average.
_BELL_CURVE_CHECKS = {
    "broken_sentences": 0.39,
    "self_correction": 0.20,
    "sentence_length_range": 0.71,
    "paragraph_length_variance": 0.52,
    "banned_words": 0.73,
}


def _bell_curve(raw_score, center):
    """Score peaks at center (human avg), penalizes over-optimization.

    Below center: linear rise (as before).
    Above center: quadratic penalty — too much is suspicious.
    """
    if center <= 0:
        return raw_score
    if raw_score <= center:
        return raw_score / center
    else:
        overshoot = (raw_score - center) / (1.0 - center) if center < 1 else 0
        return max(0.0, 1.0 - overshoot * overshoot)


def calibrate_tiers(tier1, tier2):
    """Apply bell-curve calibration and over-optimization penalty in-place."""
    # 1. Bell-curve adjustment for over-optimizable dimensions
    for tier in [tier1, tier2]:
        for name, data in tier.items():
            if name.startswith("_"):
                continue
            if name in _BELL_CURVE_CHECKS:
                raw = data["score"]
                center = _BELL_CURVE_CHECKS[name]
                calibrated = round(max(0.0, min(1.0, _bell_curve(raw, center))), 4)
                data["raw_score"] = raw
                data["score"] = calibrated
                data["detail"] += f" [calibrated from {raw:.2f}, center={center}]"

    # 2. Over-optimization penalty: if 60%+ of checks score > 0.8,
    #    the article is suspiciously "perfect" — apply global penalty.
    all_scores = []
    for tier in [tier1, tier2]:
        for name, data in tier.items():
            if not name.startswith("_"):
                all_scores.append(data["score"])

    high_count = sum(1 for s in all_scores if s > 0.8)
    over_opt_ratio = high_count / len(all_scores) if all_scores else 0
    penalty = 1.0
    if over_opt_ratio >= 0.6:
        penalty = 0.85  # 15% penalty for suspiciously perfect articles

    if penalty < 1.0:
        for tier in [tier1, tier2]:
            for name, data in tier.items():
                if not name.startswith("_"):
                    data["score"] = round(data["score"] * penalty, 4)

    # 3. Recalculate tier summaries
    for tier in [tier1, tier2]:
        scores = [data["score"] for name, data in tier.items() if not name.startswith("_")]
        tier["_summary"]["mean_score"] = round(sum(scores) / len(scores), 4) if scores else 0
        tier["_summary"]["scores"] = [round(s, 4) for s in scores]

    return penalty


# ============================================================
# Composite Score
# ============================================================

def compute_composite(tier1, tier2, tier3_score=None, tech_mode=False, tech_tier=None):
    """Compute composite score (0=human, 100=AI).

    Normal mode:
      With tier3: T1=50%, T2=30%, T3=20%
      Without:    T1=62.5%, T2=37.5%

    Tech mode:
      T1=30%, T2=15%, Tech=40%, T3=15%
      (Tech checks replace generic anti-AI dimensions that don't apply to technical writing)
    """
    if tech_mode and tech_tier is not None:
        t1_mean = tier1["_summary"]["mean_score"]
        t2_mean = tier2["_summary"]["mean_score"]
        tech_mean = tech_tier["_summary"]["mean_score"]

        if tier3_score is not None:
            humanness = t1_mean * 0.30 + t2_mean * 0.15 + tech_mean * 0.40 + tier3_score * 0.15
            weights = {"tier1": 0.30, "tier2": 0.15, "tech": 0.40, "tier3": 0.15}
        else:
            humanness = t1_mean * 0.35 + t2_mean * 0.20 + tech_mean * 0.45
            weights = {"tier1": 0.35, "tier2": 0.20, "tech": 0.45}

        composite = round((1 - humanness) * 100, 2)
        return composite, weights

    t1_mean = tier1["_summary"]["mean_score"]
    t2_mean = tier2["_summary"]["mean_score"]

    if tier3_score is not None:
        humanness = t1_mean * 0.50 + t2_mean * 0.30 + tier3_score * 0.20
        weights = {"tier1": 0.50, "tier2": 0.30, "tier3": 0.20}
    else:
        humanness = t1_mean * 0.625 + t2_mean * 0.375
        weights = {"tier1": 0.625, "tier2": 0.375}

    composite = round((1 - humanness) * 100, 2)
    return composite, weights


def build_param_scores(tier1, tier2):
    """Build flat param→score map for optimization. Averages if multiple checks map to same param."""
    param_map = {}
    for tier in [tier1, tier2]:
        for name, data in tier.items():
            if name.startswith("_"):
                continue
            param = data.get("param")
            if param is None:
                continue
            if param not in param_map:
                param_map[param] = []
            param_map[param].append(data["score"])
    return {p: round(sum(scores) / len(scores), 4) for p, scores in param_map.items()}


# ============================================================
# Main API
# ============================================================

def score_article(text, verbose=False, tier3_score=None, tech_mode=False):
    """Score an article. Returns full results dict.

    When tech_mode=True:
      - Tier1/Tier2 checks that penalise legitimate tech-writing patterns
        (negative emotion, temperature mix) are still computed but marked
        with zero weight so the agent can see them without being punished.
      - Five tech-specific checks (version refs, code blocks, honesty markers,
        concrete measurements, pitfall records) are run and carry 40-45% weight.
    """
    clean = re.sub(r'^#+\s+.*$', '', text, flags=re.MULTILINE).strip()

    tier1 = run_tier(TIER1_CHECKS, clean)
    tier2 = run_tier(TIER2_CHECKS, clean)

    tech_tier = None
    if tech_mode:
        # Zero out scores for checks that don't apply to tech articles,
        # so they don't poison the composite but the raw data stays visible.
        for check_name in TECH_SKIP_CHECKS:
            for tier in [tier1, tier2]:
                if check_name in tier:
                    tier[check_name]["score"] = 0.0
                    tier[check_name]["detail"] += " [SKIPPED: tech mode]"

        # Run tech-specific checks
        tech_tier = {"_summary": {"count": 0, "mean_score": 0.0, "scores": []}}
        tech_scores = []
        for name, fn_name in TECH_CHECKS:
            fn = globals()[fn_name]
            r = fn(clean)
            tech_tier[name] = r
            tech_scores.append(r["score"])
        tech_tier["_summary"] = {
            "count": len(TECH_CHECKS),
            "mean_score": round(sum(tech_scores) / len(tech_scores), 4) if tech_scores else 0,
            "scores": [round(s, 4) for s in tech_scores],
        }

    over_opt_penalty = calibrate_tiers(tier1, tier2)
    composite, weights = compute_composite(
        tier1, tier2, tier3_score,
        tech_mode=tech_mode, tech_tier=tech_tier,
    )
    param_scores = build_param_scores(tier1, tier2)
    if tech_tier is not None:
        param_scores["tech_check_mean"] = tech_tier["_summary"]["mean_score"]

    result = {
        "composite_score": composite,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": {
            "score": tier3_score,
            "source": "agent" if tier3_score is not None else "not_available",
        },
        "weights": weights,
        "param_scores": param_scores,
        "over_optimization_penalty": over_opt_penalty,
        "char_count": len(clean),
        "tech_mode": tech_mode,
    }
    if tech_tier is not None:
        result["tech_tier"] = tech_tier

    if verbose:
        _print_verbose(result)

    return result


def _print_verbose(result):
    """Print a human-readable report."""
    composite = result["composite_score"]
    print(f"\n{'=' * 60}")
    print(f"HUMANNESS SCORE: {composite:.1f}/100 (lower = more human)")
    print(f"{'=' * 60}")

    for tier_name, tier_label, weight in [
        ("tier1", "Tier 1 — Statistical", result["weights"].get("tier1", 0)),
        ("tier2", "Tier 2 — Pattern", result["weights"].get("tier2", 0)),
    ]:
        tier = result[tier_name]
        summary = tier["_summary"]
        print(f"\n{tier_label} (weight {weight:.0%}, mean {summary['mean_score']:.2f})")
        for name, data in tier.items():
            if name.startswith("_"):
                continue
            bar = "█" * int(data["score"] * 10) + "░" * (10 - int(data["score"] * 10))
            param_tag = f" [{data['param']}]" if data.get("param") else ""
            print(f"  {bar} {data['score']:.2f}  {name}{param_tag}")
            print(f"         {data['detail']}")

    t3 = result["tier3"]
    if t3["score"] is not None:
        t3_weight = result["weights"].get("tier3", 0)
        print(f"\nTier 3 — LLM (weight {t3_weight:.0%})")
        print(f"  Score: {t3['score']:.2f} (source: {t3['source']})")
    else:
        print(f"\nTier 3 — LLM: not available (standalone mode)")

    tech_tier = result.get("tech_tier")
    if tech_tier:
        tech_weight = result["weights"].get("tech", 0)
        summary = tech_tier["_summary"]
        print(f"\nTech Tier — Authenticity (weight {tech_weight:.0%}, mean {summary['mean_score']:.2f})")
        for name, data in tech_tier.items():
            if name.startswith("_"):
                continue
            bar = "█" * int(data["score"] * 10) + "░" * (10 - int(data["score"] * 10))
            print(f"  {bar} {data['score']:.2f}  {name}")
            print(f"         {data['detail']}")

    print(f"\nComposite: {composite:.1f} (0=完美人类, 100=明显AI)")
    print(f"Weights: {result['weights']}")

    param_scores = result["param_scores"]
    if param_scores:
        sorted_params = sorted(param_scores.items(), key=lambda x: x[1])
        print(f"\nLowest-scoring parameters (optimize these first):")
        for param, score in sorted_params[:3]:
            print(f"  {param}: {score:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Score article humanness (0=human, 100=AI)")
    parser.add_argument("input", help="Markdown article file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detailed report")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--tier3", type=float, default=None,
                        help="Tier 3 LLM score (0-1), passed by agent from SKILL.md")
    parser.add_argument("--tech", action="store_true",
                        help="Tech article mode: skip emotion/temperature checks, "
                             "add code/version/honesty/measurement/pitfall checks")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    result = score_article(text, verbose=args.verbose, tier3_score=args.tier3,
                           tech_mode=args.tech)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not args.verbose:
        print(f"{result['composite_score']:.1f}")


if __name__ == "__main__":
    main()
