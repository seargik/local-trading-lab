from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .trend_lifecycle import TrendLifecycleResult, classify_trend_lifecycle

POLICY_PATH = Path("config/market_state_router_policy.json")


@dataclass
class MarketStateResult:
    symbol: str
    analysis_timeframe: str
    lifecycle_state: str
    state_label: str
    direction: str
    trend_strength: str
    volatility_state: str
    structure_state: str
    htf_alignment: str
    lifecycle_confidence: float
    confidence: float
    preferred_strategy_family: str
    router_action: str
    route_direction: str
    risk_multiplier: float
    entry_mode: str
    exit_family: str
    reason: list[str]
    metrics: dict[str, Any]
    policy_version: str
    identified_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_market_state_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("market-state router policy must be a JSON object")
    return payload


def _num(payload: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = payload.get(key, default)
        if value is None or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _state_label(state: str) -> str:
    return str(state or "unknown").replace("_", " ").strip().title()


def _structure_state(features: dict[str, Any]) -> str:
    raw = _text(features, "market_structure")
    low = raw.lower()
    normalized = low.replace("-", "_").replace("/", "_").replace(" ", "_")
    parts = {part for part in normalized.split("_") if part}
    bullish = "bull" in low or "bullish" in low or bool(parts.intersection({"hh", "hl", "higherhigh", "higherlow"}))
    bearish = "bear" in low or "bearish" in low or bool(parts.intersection({"lh", "ll", "lowerhigh", "lowerlow"}))
    if bullish and not bearish:
        return "BULLISH"
    if bearish and not bullish:
        return "BEARISH"
    return raw.upper() if raw else "MIXED"


def _htf_state(features: dict[str, Any], htf_context: dict[str, Any], direction: str) -> tuple[str, str]:
    raw = _text(features, "htf_alignment") or _text(htf_context, "htf_alignment")
    low = raw.lower()
    bullish = any(token in low for token in ["bull", "up", "long"])
    bearish = any(token in low for token in ["bear", "down", "short"])
    direction = str(direction or "MIXED").upper()
    if direction == "LONG":
        if bullish and not bearish:
            return "ALIGNED", raw or "bullish HTF"
        if bearish and not bullish:
            return "CONFLICTING", raw or "bearish HTF"
    if direction == "SHORT":
        if bearish and not bullish:
            return "ALIGNED", raw or "bearish HTF"
        if bullish and not bearish:
            return "CONFLICTING", raw or "bullish HTF"
    return "MIXED", raw or "mixed / unavailable"


def _structure_alignment(structure: str, direction: str) -> str:
    direction = str(direction or "MIXED").upper()
    if direction == "LONG" and structure == "BULLISH":
        return "ALIGNED"
    if direction == "SHORT" and structure == "BEARISH":
        return "ALIGNED"
    if direction == "LONG" and structure == "BEARISH":
        return "CONFLICTING"
    if direction == "SHORT" and structure == "BULLISH":
        return "CONFLICTING"
    return "MIXED"


def _trend_strength(features: dict[str, Any], state: str) -> str:
    if state == "panic_volatility":
        return "UNSTABLE"
    trend_score = _num(features, "trend_regime_score")
    adx = _num(features, "adx_14")
    if trend_score >= 68 and adx >= 30:
        return "STRONG"
    if trend_score >= 55 and adx >= 22:
        return "MODERATE"
    return "WEAK"


def _volatility_state(features: dict[str, Any], state: str, policy: dict[str, Any]) -> str:
    cfg = policy.get("volatility") or {}
    atr = _num(features, "atr_pct")
    panic = _num(features, "panic_regime_score")
    if state == "panic_volatility" or panic >= float(cfg.get("panic_score_extreme", 70)) or atr >= float(cfg.get("atr_extreme", 0.045)):
        return "EXTREME"
    if state == "compression_building":
        return "COMPRESSED"
    if atr >= float(cfg.get("atr_high", 0.03)):
        return "HIGH"
    if atr > 0 and atr <= float(cfg.get("atr_low", 0.008)):
        return "LOW"
    return "NORMAL"


def _refine_exhaustion(state: str, direction: str, features: dict[str, Any], reasons: list[str]) -> str:
    if state != "trend_extended_late":
        return state
    if direction == "LONG" and (_bool(features, "bearish_divergence") or _bool(features, "choch_bearish")):
        reasons.append("extended bullish trend also shows bearish divergence/change-of-character")
        return "trend_exhaustion"
    if direction == "SHORT" and (_bool(features, "bullish_divergence") or _bool(features, "choch_bullish")):
        reasons.append("extended bearish trend also shows bullish divergence/change-of-character")
        return "trend_exhaustion"
    return state


def _confidence(
    base: float,
    direction: str,
    htf_state: str,
    structure_alignment: str,
    policy: dict[str, Any],
    reasons: list[str],
) -> float:
    cfg = policy.get("confidence") or {}
    value = float(base or 0)
    if htf_state == "ALIGNED":
        value += float(cfg.get("htf_alignment_bonus", 5))
        reasons.append("higher timeframe agrees with direction")
    elif htf_state == "CONFLICTING":
        value -= float(cfg.get("htf_conflict_penalty", 10))
        reasons.append("higher timeframe conflicts with direction")
    if structure_alignment == "ALIGNED":
        value += float(cfg.get("structure_alignment_bonus", 5))
        reasons.append("market structure agrees with direction")
    if str(direction).upper() == "MIXED":
        value -= float(cfg.get("mixed_direction_penalty", 5))
    return round(max(0.0, min(100.0, value)), 2)


def _risk_for_confidence(confidence: float, policy: dict[str, Any]) -> float:
    for row in policy.get("risk_by_confidence") or []:
        if float(row.get("min", 0)) <= confidence <= float(row.get("max", 100)):
            return float(row.get("multiplier", 0.0))
    return 0.0


def _route_direction(
    direction_mode: str,
    lifecycle_direction: str,
    features: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[str, str | None]:
    if direction_mode == "none":
        return "NONE", None
    if direction_mode == "lifecycle":
        direction = str(lifecycle_direction or "MIXED").upper()
        return direction if direction in {"LONG", "SHORT"} else "MIXED", None
    if direction_mode == "range_edge":
        cfg = policy.get("range_edges") or {}
        lower = float(cfg.get("lower", 0.20))
        upper = float(cfg.get("upper", 0.80))
        pos = _num(features, "range_position_20", 0.5)
        if pos <= lower:
            return "LONG", "price is near the lower edge of the recent range"
        if pos >= upper:
            return "SHORT", "price is near the upper edge of the recent range"
        return "MIXED", "price is in the middle of the recent range"
    return "MIXED", None


def _lifecycle_from_analysis(
    analysis: dict[str, Any],
    features: dict[str, Any],
    htf_context: dict[str, Any],
    symbol: str,
    analysis_timeframe: str,
) -> TrendLifecycleResult:
    existing = analysis.get("lifecycle")
    fields = list(TrendLifecycleResult.__dataclass_fields__)
    if isinstance(existing, dict) and set(fields).issubset(existing):
        return TrendLifecycleResult(**{field: existing.get(field) for field in fields})
    return classify_trend_lifecycle(features, htf_context, symbol=symbol, analysis_tf=analysis_timeframe)


def identify_market_state(
    analysis: dict[str, Any] | None,
    *,
    symbol: str = "",
    analysis_timeframe: str = "",
    policy: dict[str, Any] | None = None,
) -> MarketStateResult:
    analysis = analysis or {}
    features = dict(analysis.get("features") or {})
    htf_context = dict(analysis.get("htf_context") or {})
    policy = dict(policy or load_market_state_policy())

    lifecycle = _lifecycle_from_analysis(analysis, features, htf_context, symbol, analysis_timeframe)
    reasons = list(lifecycle.reason or [])
    direction = str(lifecycle.trend_direction or "MIXED").upper()
    state = _refine_exhaustion(lifecycle.lifecycle_state, direction, features, reasons)
    structure = _structure_state(features)
    htf_state, htf_raw = _htf_state(features, htf_context, direction)
    structure_fit = _structure_alignment(structure, direction)
    confidence = _confidence(lifecycle.confidence, direction, htf_state, structure_fit, policy, reasons)
    strength = _trend_strength(features, state)
    volatility = _volatility_state(features, state, policy)

    route_cfg = dict((policy.get("state_routes") or {}).get(state) or {})
    preferred_family = str(route_cfg.get("preferred_family") or "none")
    action = str(route_cfg.get("action") or "WAIT")
    route_direction, range_reason = _route_direction(
        str(route_cfg.get("direction_mode") or "none"), direction, features, policy
    )
    if range_reason:
        reasons.append(range_reason)

    min_candidate = float((policy.get("confidence") or {}).get("min_candidate", 55))
    candidate = action == "TRADE_CANDIDATE" or action == "RANGE_EDGE_ONLY"
    if action == "RANGE_EDGE_ONLY":
        action = "TRADE_CANDIDATE" if route_direction in {"LONG", "SHORT"} else "WAIT_RANGE_MID"
        candidate = action == "TRADE_CANDIDATE"
    if candidate and route_direction not in {"LONG", "SHORT"}:
        action = "WAIT_DIRECTION_UNCLEAR"
        candidate = False
        reasons.append("router has no clear LONG/SHORT direction")
    if candidate and confidence < min_candidate:
        action = "WAIT_LOW_CONFIDENCE"
        candidate = False
        reasons.append(f"confidence {confidence:.0f}% is below {min_candidate:.0f}% candidate floor")

    risk = 0.0
    if candidate:
        risk = min(_risk_for_confidence(confidence, policy), float(route_cfg.get("max_risk", 1.0)))
        reasons.append(f"risk is capped at {risk:.2f}x by confidence and state")

    exit_family = "reversal_defensive" if state == "trend_exhaustion" else lifecycle.exit_family
    metrics = {
        "close": features.get("close"),
        "trend_regime_score": _num(features, "trend_regime_score"),
        "range_regime_score": _num(features, "range_regime_score"),
        "squeeze_regime_score": _num(features, "squeeze_regime_score"),
        "panic_regime_score": _num(features, "panic_regime_score"),
        "adx_14": _num(features, "adx_14"),
        "rsi_14": _num(features, "rsi_14", 50.0),
        "atr_pct": _num(features, "atr_pct"),
        "bb_width_pct": _num(features, "bb_width_pct"),
        "range_position_20": _num(features, "range_position_20", 0.5),
        "market_structure_raw": _text(features, "market_structure"),
        "htf_alignment_raw": htf_raw,
        "structure_alignment": structure_fit,
        "lifecycle_allowed_families": list(lifecycle.allowed_strategy_families or []),
    }

    return MarketStateResult(
        symbol=symbol or lifecycle.symbol,
        analysis_timeframe=analysis_timeframe or lifecycle.analysis_tf,
        lifecycle_state=state,
        state_label=_state_label(state),
        direction=direction,
        trend_strength=strength,
        volatility_state=volatility,
        structure_state=structure,
        htf_alignment=htf_state,
        lifecycle_confidence=round(float(lifecycle.confidence or 0), 2),
        confidence=confidence,
        preferred_strategy_family=preferred_family,
        router_action=action,
        route_direction=route_direction,
        risk_multiplier=round(float(risk), 2),
        entry_mode=lifecycle.entry_mode,
        exit_family=exit_family,
        reason=list(dict.fromkeys(reasons))[:12],
        metrics=metrics,
        policy_version=str(policy.get("version") or ""),
        identified_at=datetime.now(timezone.utc).isoformat(),
    )


def identify_analysis_map(
    analysis_map: dict[str, dict[str, Any]],
    *,
    analysis_timeframe: str = "",
    policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, analysis in (analysis_map or {}).items():
        if not isinstance(analysis, dict):
            continue
        result = identify_market_state(analysis, symbol=str(symbol), analysis_timeframe=analysis_timeframe, policy=policy)
        row = result.to_dict()
        row["reason"] = " | ".join(result.reason)
        row["close"] = result.metrics.get("close")
        rows.append(row)
    return pd.DataFrame(rows)
