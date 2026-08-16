from __future__ import annotations

from typing import Any


def _num(features: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = features.get(key, default)
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _flag(features: dict[str, Any], key: str) -> bool:
    value = features.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def regime_group(regime_detail: str) -> str:
    text = str(regime_detail or "").lower()
    if "trend" in text:
        return "trend"
    if "breakout" in text or "breakdown" in text or "expansion" in text:
        return "breakout_expansion"
    if "compression" in text or "squeeze" in text:
        return "compression"
    if "range" in text or "chop" in text:
        return "range_chop"
    if "sweep" in text or "exhaustion" in text or "reversal" in text:
        return "reversal_sweep"
    if "panic" in text or "high_vol" in text or "liquidation" in text:
        return "panic_high_vol"
    return "mixed_unknown"


def classify_detailed_regime(features: dict[str, Any]) -> dict[str, Any]:
    """V25 evidence label for backtest analytics.

    This deliberately does not replace the older broad `regime` label.  It adds a
    more descriptive split for analytics/calibration: trend vs range vs squeeze,
    direction, breakout/sweep state, and high-volatility stress.
    """
    adx = _num(features, "adx_14")
    atr_pct = _num(features, "atr_pct")
    bb_width = _num(features, "bb_width_pct")
    trend_score = _num(features, "trend_regime_score")
    range_score = _num(features, "range_regime_score")
    squeeze_score = _num(features, "squeeze_regime_score")
    panic_score = _num(features, "panic_regime_score")
    range_pos = _num(features, "range_position_20", 0.5)
    close_strength = _num(features, "breakout_close_strength", 0.5)
    local_trend = str(features.get("local_trend") or "mixed").lower()
    htf_alignment = str(features.get("htf_alignment") or "mixed").lower()

    breakout_up = _flag(features, "breakout_above_n_bar_high")
    breakout_down = _flag(features, "breakout_below_n_bar_low")
    compression_breakout = _flag(features, "compression_before_breakout")
    sweep_high = _flag(features, "liquidity_sweep_high")
    sweep_low = _flag(features, "liquidity_sweep_low")
    retest_success = _flag(features, "retest_success_flag")

    reason_parts: list[str] = []

    # Extreme stress first, because it changes how normal trend/range evidence should be read.
    if panic_score >= 70 or (atr_pct >= 0.055 and adx >= 20):
        label = "panic_high_volatility"
        reason_parts.append(f"panic_score={panic_score:.0f}, atr_pct={atr_pct:.3f}")
    elif sweep_high:
        label = "liquidity_sweep_high_reversal_risk"
        reason_parts.append("swept prior high and closed back below")
    elif sweep_low:
        label = "liquidity_sweep_low_reversal_risk"
        reason_parts.append("swept prior low and closed back above")
    elif breakout_up and compression_breakout:
        label = "compression_breakout_up"
        reason_parts.append("upside breakout after compression")
    elif breakout_down and compression_breakout:
        label = "compression_breakdown_down"
        reason_parts.append("downside breakdown after compression")
    elif breakout_up:
        label = "bullish_breakout_expansion" if close_strength >= 0.55 else "weak_bullish_breakout"
        reason_parts.append(f"breakout_up, close_strength={close_strength:.2f}")
    elif breakout_down:
        label = "bearish_breakdown_expansion" if close_strength <= 0.45 else "weak_bearish_breakdown"
        reason_parts.append(f"breakout_down, close_strength={close_strength:.2f}")
    elif squeeze_score >= 70 and bb_width <= 0.04:
        label = "compression_squeeze_wait"
        reason_parts.append(f"squeeze_score={squeeze_score:.0f}, bb_width={bb_width:.3f}")
    elif trend_score >= 60 and adx >= 20 and local_trend == "up":
        label = "aligned_bull_trend" if htf_alignment == "bullish" else "local_bull_trend_mixed_htf"
        reason_parts.append(f"trend_score={trend_score:.0f}, adx={adx:.1f}, htf={htf_alignment}")
    elif trend_score >= 60 and adx >= 20 and local_trend == "down":
        label = "aligned_bear_trend" if htf_alignment == "bearish" else "local_bear_trend_mixed_htf"
        reason_parts.append(f"trend_score={trend_score:.0f}, adx={adx:.1f}, htf={htf_alignment}")
    elif range_score >= 60:
        if range_pos >= 0.78:
            label = "range_upper_edge"
        elif range_pos <= 0.22:
            label = "range_lower_edge"
        else:
            label = "range_mid_chop"
        reason_parts.append(f"range_score={range_score:.0f}, range_pos={range_pos:.2f}")
    elif retest_success:
        label = "post_breakout_retest_hold"
        reason_parts.append("recent breakout retest held")
    else:
        label = "mixed_chop_unclear"
        reason_parts.append(f"trend={trend_score:.0f}, range={range_score:.0f}, squeeze={squeeze_score:.0f}")

    return {
        "regime_detail": label,
        "regime_group": regime_group(label),
        "regime_reason": "; ".join(reason_parts[:3]),
        "trend_regime_score": round(trend_score, 2),
        "range_regime_score": round(range_score, 2),
        "squeeze_regime_score": round(squeeze_score, 2),
        "panic_regime_score": round(panic_score, 2),
    }
