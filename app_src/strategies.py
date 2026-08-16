from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class StrategyOpinion:
    strategy_name: str
    strategy_id: int
    version_id: int
    version_no: int
    slot_id: int
    template_key: str
    analyze: bool
    enabled: bool
    bias: str
    score: float
    threshold: float
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "version_no": self.version_no,
            "slot_id": self.slot_id,
            "template_key": self.template_key,
            "analyze": self.analyze,
            "enabled": self.enabled,
            "bias": self.bias,
            "score": round(float(self.score), 2),
            "threshold": round(float(self.threshold), 2),
            "note": self.note,
        }


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _coerce_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _resolve_value(features: dict[str, Any], raw: Any) -> Any:
    if isinstance(raw, str) and raw in features:
        return features.get(raw)
    return raw


def _compare_values(left: Any, operator: str, right: Any, right_2: Any | None = None) -> bool:
    if operator in {"true", "is_true"}:
        return bool(left) is True
    if operator in {"false", "is_false"}:
        return bool(left) is False

    left_num = _coerce_number(left)
    right_num = _coerce_number(right)
    right2_num = _coerce_number(right_2)

    if operator == "between":
        if left_num is not None and right_num is not None and right2_num is not None:
            lo, hi = sorted([right_num, right2_num])
            return lo <= left_num <= hi
        left_str = str(left)
        return str(right) <= left_str <= str(right_2)
    if operator == "contains":
        return str(right).lower() in str(left).lower()

    if left_num is not None and right_num is not None:
        if operator == ">":
            return left_num > right_num
        if operator == ">=":
            return left_num >= right_num
        if operator == "<":
            return left_num < right_num
        if operator == "<=":
            return left_num <= right_num
        if operator == "==":
            return left_num == right_num
        if operator == "!=":
            return left_num != right_num

    if operator == "==":
        return str(left) == str(right)
    if operator == "!=":
        return str(left) != str(right)
    if operator == ">":
        return str(left) > str(right)
    if operator == ">=":
        return str(left) >= str(right)
    if operator == "<":
        return str(left) < str(right)
    if operator == "<=":
        return str(left) <= str(right)
    return False


def score_rule_builder(features: dict[str, Any], params: dict[str, Any], indicator_rules: list[dict[str, Any]] | None = None) -> tuple[str, float, str]:
    rules = indicator_rules or params.get("indicator_rules") or []
    threshold = float(params.get("score_threshold", 70))
    long_score = 0.0
    short_score = 0.0
    watch_score = 0.0
    notes: list[str] = []
    for rule in rules:
        if not rule or not rule.get("enabled", True):
            continue
        indicator = str(rule.get("indicator") or "").strip()
        if not indicator:
            continue
        operator = str(rule.get("operator") or ">=").strip()
        left = features.get(indicator)
        right = _resolve_value(features, rule.get("value"))
        right_2 = _resolve_value(features, rule.get("value_2"))
        matched = _compare_values(left, operator, right, right_2)
        if matched:
            weight = float(rule.get("weight", 10) or 10)
            bias = str(rule.get("bias") or "BOTH").upper()
            if bias == "LONG":
                long_score += weight
            elif bias == "SHORT":
                short_score += weight
            elif bias == "WATCH":
                watch_score += weight
            else:
                long_score += weight / 2
                short_score += weight / 2
            notes.append(f"{indicator} {operator} {right}{('..'+str(right_2)) if operator=='between' else ''}")
    if long_score >= short_score and long_score >= threshold:
        return "LONG", clamp(long_score), "; ".join(notes[:5]) or "Rule builder LONG"
    if short_score > long_score and short_score >= threshold:
        return "SHORT", clamp(short_score), "; ".join(notes[:5]) or "Rule builder SHORT"
    if watch_score >= threshold or max(long_score, short_score, watch_score) >= threshold:
        return "WATCH", clamp(max(long_score, short_score, watch_score)), "; ".join(notes[:5]) or "Rule builder WATCH"
    return "WAIT", clamp(max(long_score, short_score, watch_score)), "; ".join(notes[:5]) or "Rule builder waiting"


def classify_regime(features: dict[str, Any]) -> str:
    adx_value = features.get("adx_14") or 0.0
    bb_width = features.get("bb_width_pct") or 0.0
    atr_pct = features.get("atr_pct") or 0.0
    local_trend = features.get("local_trend")
    if adx_value >= 22 and local_trend == "up":
        return "Bull trend"
    if adx_value >= 22 and local_trend == "down":
        return "Bear trend"
    if bb_width <= 0.035 and atr_pct <= 0.02:
        return "Compression"
    return "Range / mixed"


def score_from_slot(features: dict[str, Any], slot: dict[str, Any]) -> StrategyOpinion:
    strategy_name = slot.get("strategy_name") or f"Slot {slot['slot_id']}"
    if not slot.get("version_id"):
        return StrategyOpinion(strategy_name, -1, -1, 0, int(slot["slot_id"]), "none", False, False, "OFF", 0.0, 100.0, "No strategy selected")

    analyze = bool(slot.get("analyze", False))
    enabled = bool(slot.get("enabled", False))
    if enabled:
        analyze = True
    if not analyze:
        return StrategyOpinion(strategy_name, int(slot["strategy_id"]), int(slot["version_id"]), int(slot["version_no"]), int(slot["slot_id"]), str(slot.get("template_key")), analyze, enabled, "OFF", 0.0, float(slot.get("score_threshold") or 70), "Analyze is OFF")

    template_key = str(slot.get("template_key") or "")
    params = slot.get("rule_params") or {}
    indicator_rules = slot.get("indicator_rules") or params.get("indicator_rules") or []
    threshold = float(slot.get("score_threshold") or params.get("score_threshold") or 70)

    scorer = TEMPLATE_SCORERS.get(template_key, score_generic_template)
    if template_key == "rule_builder":
        bias, score, note = scorer(features, params, indicator_rules)
    else:
        bias, score, note = scorer(features, params)
        if indicator_rules:
            overlay_bias, overlay_score, overlay_note = score_rule_builder(features, {**params, "score_threshold": threshold}, indicator_rules)
            overlay_weight = float(params.get("rule_overlay_weight", 0.35))
            score = clamp(score * (1 - overlay_weight) + overlay_score * overlay_weight)
            if overlay_bias in {"LONG", "SHORT", "WATCH"} and bias == "WAIT":
                bias = overlay_bias
            note = f"{note} | overlay: {overlay_note}"
    if score < threshold and bias in {"LONG", "SHORT"}:
        bias = "WAIT"
    return StrategyOpinion(
        strategy_name=strategy_name,
        strategy_id=int(slot["strategy_id"]),
        version_id=int(slot["version_id"]),
        version_no=int(slot["version_no"]),
        slot_id=int(slot["slot_id"]),
        template_key=template_key,
        analyze=analyze,
        enabled=enabled,
        bias=bias,
        score=clamp(score),
        threshold=threshold,
        note=note,
    )


def score_bull_trend(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    score = 0.0
    score += params.get("trend_weight", 30) if (features.get("ema_20", 0) > features.get("ema_50", 0)) else 0
    score += params.get("adx_weight", 20) if (features.get("adx_14", 0) >= 20) else max(0.0, (features.get("adx_14", 0) - 10))
    rsi_v = features.get("rsi_14", 50)
    score += params.get("rsi_weight", 20) if 45 <= rsi_v <= 68 else max(0.0, 10 - abs(rsi_v - 56))
    score += params.get("htf_weight", 15) if features.get("htf_alignment") == "bullish" else 0
    score += params.get("volume_weight", 10) if (features.get("volume_ratio", 0) >= 1.0) else 0
    score += params.get("structure_weight", 5) if features.get("market_structure") == "higher_high_higher_low" else 0
    score += 5 if (features.get("distance_to_ma200_pct", -1) > 0) else 0
    bias = "LONG" if score >= float(params.get("score_threshold", 70)) else "WAIT"
    return bias, score, "Trend-following pullback/continuation score"


def score_bear_breakdown(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    score = 0.0
    score += params.get("trend_weight", 30) if (features.get("ema_20", 0) < features.get("ema_50", 0)) else 0
    score += params.get("adx_weight", 20) if (features.get("adx_14", 0) >= 20) else max(0.0, (features.get("adx_14", 0) - 10))
    rsi_v = features.get("rsi_14", 50)
    score += params.get("rsi_weight", 20) if 32 <= rsi_v <= 55 else max(0.0, 10 - abs(rsi_v - 44))
    score += params.get("htf_weight", 15) if features.get("htf_alignment") == "bearish" else 0
    score += params.get("volume_weight", 10) if (features.get("volume_ratio", 0) >= 1.0) else 0
    score += params.get("structure_weight", 5) if features.get("market_structure") == "lower_high_lower_low" else 0
    score += 5 if (features.get("distance_to_ma200_pct", 1) < 0) else 0
    bias = "SHORT" if score >= float(params.get("score_threshold", 70)) else "WAIT"
    return bias, score, "Downtrend rally-fail / breakdown score"


def score_sideways_compression(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    bb_width = features.get("bb_width_pct") or 0.0
    atr_pct = features.get("atr_pct") or 0.0
    volume_ratio = features.get("volume_ratio") or 0.0
    imbalance = abs(features.get("order_book_imbalance") or 0.0)
    score = 0.0
    score += clamp((0.05 - bb_width) * 1200, 0, 40)
    score += clamp((0.03 - atr_pct) * 1200, 0, 30)
    score += clamp((1.2 - volume_ratio) * 25, 0, 15)
    score += clamp((0.20 - imbalance) * 100, 0, 10)
    score += 5 if (features.get("liquidity_sweep_high") or features.get("liquidity_sweep_low")) else 0
    bias = "WATCH" if score >= float(params.get("score_threshold", 70)) else "WAIT"
    return bias, score, "Low-volatility squeeze watcher"


def score_range_reversion(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    z = features.get("range_zscore") or 0.0
    adx_value = features.get("adx_14") or 0.0
    score = 0.0
    score += clamp((25 - adx_value) * 2, 0, 30)
    score += clamp(abs(z) * 18, 0, 35)
    score += 15 if abs((features.get("trend_distance_pct") or 0.0)) < 0.035 else 0
    score += 10 if features.get("bullish_divergence") or features.get("bearish_divergence") else 0
    score += 10 if features.get("market_structure") in {"mixed", "higher_high_lower_low", "lower_high_higher_low"} else 0
    if z <= -1.2 or (features.get("bullish_divergence") and (features.get("range_position_20") or 1) < 0.25):
        bias = "LONG"
    elif z >= 1.2 or (features.get("bearish_divergence") and (features.get("range_position_20") or 0) > 0.75):
        bias = "SHORT"
    else:
        bias = "WAIT"
    return bias, score, "Mean-reversion inside an established range"


def score_breakout_continuation(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    range_pos = features.get("range_position_20") or 0.5
    slope = features.get("slope_pct_10") or 0.0
    score = 0.0
    score += 25 if features.get("volume_spike") else 0
    score += 20 if abs(slope) >= 1.0 else clamp(abs(slope) * 10, 0, 20)
    score += 15 if features.get("htf_alignment") in {"bullish", "bearish"} else 0
    score += 15 if abs(features.get("open_interest_zscore_20") or 0.0) >= 1 else 0
    score += 15 if abs(features.get("order_book_imbalance") or 0.0) <= 0.35 else 0
    score += 10 if features.get("market_structure") in {"higher_high_higher_low", "lower_high_lower_low"} else 0

    if range_pos >= 0.85 and slope > 0 and (features.get("htf_alignment") != "bearish"):
        bias = "LONG"
    elif range_pos <= 0.15 and slope < 0 and (features.get("htf_alignment") != "bullish"):
        bias = "SHORT"
    else:
        bias = "WAIT"
    return bias, score, "Expansion / breakout continuation score"


def score_generic_template(features: dict[str, Any], params: dict[str, Any]) -> tuple[str, float, str]:
    score = 0.0
    score += 20 if features.get("local_trend") == "up" else 0
    score += 20 if features.get("global_trend") == "above_ma200" else 0
    score += 20 if features.get("volume_spike") else 0
    score += 20 if (features.get("adx_14") or 0) >= 20 else 0
    score += 20 if features.get("htf_alignment") == "bullish" else 0
    bias = "LONG" if score >= float(params.get("score_threshold", 70)) else "WAIT"
    return bias, score, "Generic score fallback"


TEMPLATE_SCORERS = {
    "bull_trend_rider": score_bull_trend,
    "bear_breakdown_hunter": score_bear_breakdown,
    "sideways_compression_watcher": score_sideways_compression,
    "range_reversion_trader": score_range_reversion,
    "breakout_continuation_scout": score_breakout_continuation,
    "rule_builder": score_rule_builder,
}


TEMPLATE_OPTIONS = [
    ("bull_trend_rider", "Bull Trend Rider"),
    ("bear_breakdown_hunter", "Bear Breakdown Hunter"),
    ("sideways_compression_watcher", "Sideways Compression Watcher"),
    ("range_reversion_trader", "Range Reversion Trader"),
    ("breakout_continuation_scout", "Breakout Continuation Scout"),
    ("rule_builder", "Rule Builder (live indicator rules)"),
]


def aggregate_final_view(regime: str, opinions: list[StrategyOpinion]) -> dict[str, Any]:
    enabled = [op for op in opinions if op.enabled and op.analyze and op.version_id > 0]
    analyzed = [op for op in opinions if op.analyze and op.version_id > 0]

    if not analyzed:
        return {
            "regime": regime,
            "final_bias": "NO_ANALYSIS",
            "final_score": 0.0,
            "recommendation": "All strategy slots are off or empty",
            "best_strategy": None,
        }

    if not enabled:
        top = max(analyzed, key=lambda x: x.score)
        return {
            "regime": regime,
            "final_bias": "OBSERVATION_ONLY",
            "final_score": round(top.score, 2),
            "recommendation": f"Observation mode only; strongest idea is {top.strategy_name} v{top.version_no} ({top.bias})",
            "best_strategy": top.strategy_name,
        }

    directional = [op for op in enabled if op.bias in {"LONG", "SHORT"}]
    if directional:
        top = max(directional, key=lambda x: x.score)
        recommendation = f"{top.bias} candidate from {top.strategy_name} v{top.version_no}"
        return {
            "regime": regime,
            "final_bias": top.bias,
            "final_score": round(top.score, 2),
            "recommendation": recommendation,
            "best_strategy": top.strategy_name,
            "best_slot_id": top.slot_id,
            "best_version_id": top.version_id,
        }

    top = max(enabled, key=lambda x: x.score)
    return {
        "regime": regime,
        "final_bias": top.bias,
        "final_score": round(top.score, 2),
        "recommendation": f"No directional setup yet; {top.strategy_name} v{top.version_no} says {top.bias}",
        "best_strategy": top.strategy_name,
        "best_slot_id": top.slot_id,
        "best_version_id": top.version_id,
    }
