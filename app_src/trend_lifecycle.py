from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_RULES_PATH = Path("config/trend_lifecycle_rules.json")


@dataclass
class TrendLifecycleResult:
    symbol: str
    analysis_tf: str
    lifecycle_state: str
    trend_direction: str
    confidence: float
    allowed_strategy_families: list[str]
    blocked_strategy_families: list[str]
    entry_mode: str
    exit_family: str
    reason: list[str]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_rules(path: str | Path | None = None) -> dict[str, Any]:
    rules_path = Path(path or DEFAULT_RULES_PATH)
    try:
        if rules_path.exists():
            return json.loads(rules_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"thresholds": {}, "state_family_map": {}}


def _num(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = payload.get(key, default)
    try:
        if value is None or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _text(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    return str(value)


def _bool(payload: dict[str, Any], key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _direction_from_context(features: dict[str, Any], htf_context: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    htf_context = htf_context or {}
    reasons: list[str] = []
    score = 0

    ma_stack = _text(features, "ma_stack_state")
    local = _text(features, "local_trend")
    global_trend = _text(features, "global_trend")
    htf_alignment = _text(features, "htf_alignment") or _text(htf_context, "htf_alignment")
    slope = _num(features, "slope_pct_10")
    ema_spread = _num(features, "ema_20_50_spread_pct")
    vwap_distance = _num(features, "vwap_distance_pct")
    rsi = _num(features, "rsi_14", 50.0)

    bullish_tokens = ("bull", "up", "above", "long")
    bearish_tokens = ("bear", "down", "below", "short")

    for label_name, value, weight in [
        ("MA stack", ma_stack, 2),
        ("local trend", local, 1),
        ("global trend", global_trend, 1),
        ("HTF alignment", htf_alignment, 2),
    ]:
        low = value.lower()
        if any(tok in low for tok in bullish_tokens):
            score += weight
            reasons.append(f"{label_name} is bullish ({value})")
        elif any(tok in low for tok in bearish_tokens):
            score -= weight
            reasons.append(f"{label_name} is bearish ({value})")

    if slope > 0.1:
        score += 1
        reasons.append("EMA50 slope is positive")
    elif slope < -0.1:
        score -= 1
        reasons.append("EMA50 slope is negative")
    if ema_spread > 0.002:
        score += 1
        reasons.append("EMA20 is above EMA50")
    elif ema_spread < -0.002:
        score -= 1
        reasons.append("EMA20 is below EMA50")
    if vwap_distance > 0.002:
        score += 1
        reasons.append("price is above VWAP")
    elif vwap_distance < -0.002:
        score -= 1
        reasons.append("price is below VWAP")
    if rsi >= 58:
        score += 1
        reasons.append("RSI supports bullish momentum")
    elif rsi <= 42:
        score -= 1
        reasons.append("RSI supports bearish momentum")

    if score >= 2:
        return "LONG", reasons
    if score <= -2:
        return "SHORT", reasons
    return "MIXED", reasons or ["directional evidence is mixed"]


def classify_trend_lifecycle(
    features: dict[str, Any] | None,
    htf_context: dict[str, Any] | None = None,
    symbol: str = "",
    analysis_tf: str = "",
    rules_path: str | Path | None = None,
) -> TrendLifecycleResult:
    features = features or {}
    htf_context = htf_context or {}
    rules = _load_rules(rules_path)
    t = rules.get("thresholds", {}) or {}
    family_map = rules.get("state_family_map", {}) or {}

    reason: list[str] = []
    if not features:
        return _result(symbol, analysis_tf, "no_trade_data_missing", "MIXED", 0, family_map, ["no feature payload available"], {})

    trend_score = _num(features, "trend_regime_score")
    range_score = _num(features, "range_regime_score")
    squeeze_score = _num(features, "squeeze_regime_score")
    panic_score = _num(features, "panic_regime_score")
    adx = _num(features, "adx_14")
    rsi = _num(features, "rsi_14", 50.0)
    atr_pct = _num(features, "atr_pct")
    bb_width = _num(features, "bb_width_pct")
    vwap_distance = _num(features, "vwap_distance_pct")
    range_pos = _num(features, "range_position_20", 0.5)
    trend_distance = abs(_num(features, "trend_distance_pct"))
    breakout_up = _bool(features, "breakout_above_n_bar_high")
    breakout_down = _bool(features, "breakout_below_n_bar_low")
    sweep_high = _bool(features, "liquidity_sweep_high")
    sweep_low = _bool(features, "liquidity_sweep_low")
    volume_spike = _bool(features, "volume_spike")

    direction, direction_reasons = _direction_from_context(features, htf_context)
    reason.extend(direction_reasons[:4])

    trend_entering = float(t.get("trend_score_entering", 55))
    trend_running = float(t.get("trend_score_running", 68))
    squeeze_threshold = float(t.get("squeeze_score", 62))
    range_threshold = float(t.get("range_score", 58))
    panic_threshold = float(t.get("panic_score", 70))
    adx_trend = float(t.get("adx_trend", 22))
    adx_strong = float(t.get("adx_strong", 30))
    range_upper = float(t.get("range_upper", 0.8))
    range_lower = float(t.get("range_lower", 0.2))
    late_extension_pct = float(t.get("late_extension_pct", 0.035))
    vwap_near = float(t.get("vwap_reclaim_near_pct", 0.012))

    metrics = {
        "trend_regime_score": trend_score,
        "range_regime_score": range_score,
        "squeeze_regime_score": squeeze_score,
        "panic_regime_score": panic_score,
        "adx_14": adx,
        "rsi_14": rsi,
        "atr_pct": atr_pct,
        "bb_width_pct": bb_width,
        "vwap_distance_pct": vwap_distance,
        "range_position_20": range_pos,
        "trend_distance_pct_abs": trend_distance,
        "breakout_above_n_bar_high": breakout_up,
        "breakout_below_n_bar_low": breakout_down,
        "liquidity_sweep_high": sweep_high,
        "liquidity_sweep_low": sweep_low,
        "volume_spike": volume_spike,
    }

    # Highest risk states first.
    if panic_score >= panic_threshold or (atr_pct >= 0.045 and volume_spike):
        reason.append("panic/high-volatility score is elevated")
        return _result(symbol, analysis_tf, "panic_volatility", direction, min(95, panic_score), family_map, reason, metrics)

    if sweep_high or sweep_low:
        if sweep_high:
            reason.append("recent liquidity sweep high creates reversal/fakeout risk")
            direction = "SHORT" if direction != "LONG" else "MIXED"
        if sweep_low:
            reason.append("recent liquidity sweep low creates reversal/fakeout risk")
            direction = "LONG" if direction != "SHORT" else "MIXED"
        return _result(symbol, analysis_tf, "liquidity_sweep_reversal_risk", direction, max(55, min(90, panic_score + 10)), family_map, reason, metrics)

    # Compression and breakout transition.
    if squeeze_score >= squeeze_threshold and not (breakout_up or breakout_down):
        reason.append("compression score is high and no confirmed breakout is present")
        return _result(symbol, analysis_tf, "compression_building", "MIXED", min(90, squeeze_score), family_map, reason, metrics)

    if breakout_up or breakout_down:
        direction = "LONG" if breakout_up and not breakout_down else "SHORT" if breakout_down and not breakout_up else direction
        reason.append("n-bar breakout condition is active")
        if squeeze_score >= max(45, squeeze_threshold - 12):
            reason.append("breakout has prior compression support")
        return _result(symbol, analysis_tf, "breakout_attempt", direction, max(55, min(92, trend_score + 8)), family_map, reason, metrics)

    # Trend lifecycle.
    if trend_score >= trend_running and adx >= adx_strong:
        if trend_distance >= late_extension_pct or abs(vwap_distance) >= late_extension_pct or rsi >= 72 or rsi <= 28:
            reason.append("trend is strong but extended from fair value / momentum is stretched")
            return _result(symbol, analysis_tf, "trend_extended_late", direction, min(93, trend_score), family_map, reason, metrics)
        reason.append("trend score and ADX show a running trend")
        return _result(symbol, analysis_tf, "trend_running", direction, min(94, trend_score), family_map, reason, metrics)

    if trend_score >= trend_entering and adx >= adx_trend:
        if abs(vwap_distance) <= vwap_near or (0.35 <= range_pos <= 0.65):
            reason.append("trend evidence exists and price is near a pullback/fair-value area")
            return _result(symbol, analysis_tf, "trend_pullback_entry", direction, min(90, trend_score), family_map, reason, metrics)
        reason.append("trend evidence is forming but not yet strongly extended")
        return _result(symbol, analysis_tf, "trend_entering", direction, min(88, trend_score), family_map, reason, metrics)

    # Range/chop.
    if range_score >= range_threshold or (adx < adx_trend and 0.15 <= range_pos <= 0.85):
        if range_pos >= range_upper or range_pos <= range_lower:
            reason.append("market is range-like and price is near a range edge")
        else:
            reason.append("market is range-like / choppy and not in a trend lifecycle")
        return _result(symbol, analysis_tf, "range_chop", direction, max(45, min(85, range_score)), family_map, reason, metrics)

    reason.append("no lifecycle condition has clear dominance")
    return _result(symbol, analysis_tf, "range_chop", direction, 45, family_map, reason, metrics)


def _result(
    symbol: str,
    analysis_tf: str,
    state: str,
    direction: str,
    confidence: float,
    family_map: dict[str, Any],
    reason: list[str],
    metrics: dict[str, Any],
) -> TrendLifecycleResult:
    cfg = family_map.get(state, {}) or {}
    return TrendLifecycleResult(
        symbol=symbol,
        analysis_tf=analysis_tf,
        lifecycle_state=state,
        trend_direction=direction,
        confidence=round(float(confidence or 0), 2),
        allowed_strategy_families=list(cfg.get("allowed") or []),
        blocked_strategy_families=list(cfg.get("blocked") or []),
        entry_mode=_entry_mode_for_state(state),
        exit_family=str(cfg.get("exit_family") or "no_trade_gate"),
        reason=reason[:8],
        metrics=metrics,
    )


def _entry_mode_for_state(state: str) -> str:
    if state in {"trend_pullback_entry", "trend_entering", "breakout_attempt"}:
        return "wait_for_ltf_confirmation"
    if state == "trend_running":
        return "manage_existing_or_wait_for_next_pullback"
    if state in {"trend_extended_late", "trend_exhaustion"}:
        return "avoid_new_chase_protect_existing"
    if state == "compression_building":
        return "wait_for_expansion"
    if state == "range_chop":
        return "range_edge_only"
    if state in {"panic_volatility", "no_trade_data_missing"}:
        return "no_trade_or_manual_review"
    if state == "liquidity_sweep_reversal_risk":
        return "reversal_confirmation_only"
    return "manual_review"


def classify_analysis_map(analysis_map: dict[str, dict[str, Any]], analysis_tf: str = "") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, payload in (analysis_map or {}).items():
        features = payload.get("features") if isinstance(payload, dict) else {}
        htf_context = payload.get("htf_context") if isinstance(payload, dict) else {}
        result = classify_trend_lifecycle(features, htf_context, symbol=str(symbol), analysis_tf=analysis_tf)
        row = result.to_dict()
        row["allowed_strategy_families"] = ", ".join(row.get("allowed_strategy_families") or [])
        row["blocked_strategy_families"] = ", ".join(row.get("blocked_strategy_families") or [])
        row["reason"] = " | ".join(row.get("reason") or [])
        rows.append(row)
    return pd.DataFrame(rows)
