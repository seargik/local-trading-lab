from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .backtest_core import (
    _bundle_opinion,
    _matches_segment_filter,
    historical_enrich_features,
    is_bundle_payload,
    load_bootstrap_frame,
    resample_ohlcv,
    strategy_payload_to_slot,
    timeframe_minutes,
)
from .engine import build_trade_levels
from .exit_families import add_exit_family_to_rule_params
from .features import build_htf_row, summarize_htf_context
from .market_data import BinanceFuturesClient
from .market_state_v2816 import identify_market_state
from .regime_v25 import classify_detailed_regime
from .settings import HTF_MAP, OHLCV_STORE_ROOT
from .strategies import classify_regime, score_from_slot


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return float(default)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return float(default)


def _utc(value: Any) -> pd.Timestamp:
    return pd.to_datetime(value, utc=True, errors="coerce")


def _json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _latest_row(frame: pd.DataFrame, ts: pd.Timestamp) -> dict[str, Any] | None:
    if frame is None or frame.empty or "open_time" not in frame.columns or pd.isna(ts):
        return None
    times = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    valid = frame.loc[times.le(ts)].copy()
    if valid.empty:
        return None
    return valid.iloc[-1].to_dict()


def _policy_payload(freeze_record: dict[str, Any], name: str) -> dict[str, Any]:
    return dict((((freeze_record.get("policies") or {}).get(name) or {}).get("payload") or {}))


def _strategy_map(freeze_record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("research_family") or ""): dict(row) for row in (freeze_record.get("strategy_snapshots") or [])}


def _execution_map(freeze_record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("research_family") or ""): dict(row) for row in (freeze_record.get("execution_config_snapshots") or [])}


def _closed_feature_context(
    *,
    source_root: str | Path,
    symbol: str,
    entry_timeframe: str,
    analysis_timeframe: str,
    decision_time: pd.Timestamp,
    warmup_days: int,
) -> dict[str, Any]:
    load_start = decision_time - pd.Timedelta(days=max(30, int(warmup_days)))
    base = load_bootstrap_frame(
        source_root,
        symbol.upper(),
        timeframe=entry_timeframe,
        start=load_start,
        end=decision_time,
    ).reset_index(drop=True)
    if base.empty:
        return {"ok": False, "reason": "no_entry_history"}

    entry_df = base if entry_timeframe == "5m" else resample_ohlcv(base, entry_timeframe)
    analysis_df = base if analysis_timeframe == entry_timeframe else resample_ohlcv(base, analysis_timeframe)
    entry_features = historical_enrich_features(entry_df).frame.sort_values("open_time").reset_index(drop=True)
    analysis_features = historical_enrich_features(analysis_df).frame.sort_values("open_time").reset_index(drop=True)
    if entry_features.empty or analysis_features.empty:
        return {"ok": False, "reason": "insufficient_features"}

    entry_row = _latest_row(entry_features, decision_time)
    analysis_row = _latest_row(analysis_features, decision_time)
    if not entry_row or not analysis_row:
        return {"ok": False, "reason": "no_closed_feature_row"}

    entry_available = _utc(entry_row.get("open_time"))
    interval_minutes = max(1, timeframe_minutes(entry_timeframe))
    if pd.isna(entry_available) or abs((decision_time - entry_available).total_seconds()) > max(60.0, interval_minutes * 6.0):
        return {"ok": False, "reason": "entry_history_stale", "entry_available": None if pd.isna(entry_available) else entry_available.isoformat()}

    higher_tfs = [
        tf for tf in HTF_MAP.get(analysis_timeframe, [])
        if timeframe_minutes(tf) > timeframe_minutes(analysis_timeframe)
    ]
    higher_frames: dict[str, pd.DataFrame] = {}
    for tf in higher_tfs:
        raw = resample_ohlcv(base, tf)
        higher_frames[tf] = historical_enrich_features(raw).frame.sort_values("open_time").reset_index(drop=True)

    # Strategy scoring mirrors the V28.18 backtest information clock: entry row
    # plus the latest analysis/higher-timeframe rows already closed by the signal time.
    strategy_htf: dict[str, Any] = {analysis_timeframe: build_htf_row(analysis_row)}
    for tf, frame in higher_frames.items():
        row = _latest_row(frame, decision_time)
        if row:
            strategy_htf[tf] = build_htf_row(row)
    strategy_features = dict(entry_row)
    strategy_features.update(summarize_htf_context(strategy_htf))
    for tf, ctx in strategy_htf.items():
        for key, value in ctx.items():
            strategy_features[f"htf_{tf}_{key}"] = value

    # Market State is a 4h-style controller in the current architecture. It must
    # be computed at the analysis-bar close itself, not with a higher-timeframe
    # candle that closed later during the current 1h decision bar.
    state_time = _utc(analysis_row.get("open_time"))
    state_htf: dict[str, Any] = {}
    for tf, frame in higher_frames.items():
        row = _latest_row(frame, state_time)
        if row:
            state_htf[tf] = build_htf_row(row)
    state_features = dict(analysis_row)
    state_features.update(summarize_htf_context(state_htf))
    for tf, ctx in state_htf.items():
        for key, value in ctx.items():
            state_features[f"htf_{tf}_{key}"] = value

    return {
        "ok": True,
        "entry_row": entry_row,
        "analysis_row": analysis_row,
        "strategy_features": strategy_features,
        "strategy_htf_context": strategy_htf,
        "state_features": state_features,
        "state_htf_context": state_htf,
        "state_decision_time": state_time,
        "reference_price": _num(entry_row.get("close"), 0.0),
    }


def default_observable_quote(symbol: str, *, now: datetime | None = None) -> dict[str, Any]:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    client = BinanceFuturesClient(timeout_seconds=10)
    snapshot = client.fetch_market_snapshot(symbol, oi_period="5m")
    bid = _num(snapshot.get("best_bid"), 0.0) or None
    ask = _num(snapshot.get("best_ask"), 0.0) or None
    mark = _num(snapshot.get("mark_price"), 0.0) or None
    return {
        "symbol": symbol.upper(),
        "observed_at": observed.isoformat(),
        "source": "binance_usdm_rest",
        "best_bid": bid,
        "best_ask": ask,
        "mark_price": mark,
        "spread_bps": snapshot.get("order_book_spread_bps"),
        "executable_top_of_book": bool(bid and ask and bid > 0 and ask > 0),
    }


def normalize_quote(
    symbol: str,
    quote: dict[str, Any] | None,
    *,
    reference_price: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    value = dict(quote or {})
    observed = _utc(value.get("observed_at"))
    now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    else:
        now_ts = now_ts.tz_convert("UTC")
    bid = _num(value.get("best_bid"), 0.0)
    ask = _num(value.get("best_ask"), 0.0)
    mark = _num(value.get("mark_price"), 0.0)
    executable = bool(bid > 0 and ask > 0 and ask >= bid)
    if not executable:
        bid = ask = 0.0
    if mark <= 0:
        mark = reference_price
    if pd.isna(observed):
        observed = now_ts
    spread = _num(value.get("spread_bps"), 0.0)
    if executable and spread <= 0:
        mid = (bid + ask) / 2.0
        spread = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0
    return {
        "symbol": symbol.upper(),
        "observed_at": observed.isoformat(),
        "source": str(value.get("source") or ("closed_bar_proxy" if not executable else "quote_provider")),
        "best_bid": bid if executable else None,
        "best_ask": ask if executable else None,
        "mark_price": mark if mark > 0 else reference_price,
        "spread_bps": round(spread, 4) if spread else 0.0,
        "executable_top_of_book": executable,
        "is_proxy": not executable,
    }


def fetch_or_proxy_quote(
    symbol: str,
    *,
    reference_price: float,
    now: datetime | None = None,
    quote_provider: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provider = quote_provider or default_observable_quote
    raw: dict[str, Any] | None = None
    try:
        raw = provider(symbol, now=now)
    except TypeError:
        try:
            raw = provider(symbol)
        except Exception:
            raw = None
    except Exception:
        raw = None
    return normalize_quote(symbol, raw, reference_price=reference_price, now=now)


def _execution_spread_bps(config: dict[str, Any]) -> float:
    if config.get("spread_bps_round_trip") is not None:
        return max(0.0, _num(config.get("spread_bps_round_trip"), 0.0))
    return max(0.0, _num(config.get("spread_bps"), 0.0))


def executable_entry_price(side: str, quote: dict[str, Any], reference_price: float, config: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    side = str(side).upper()
    slippage_bps = max(0.0, _num(config.get("slippage_bps_per_side"), 0.0))
    observed = bool(quote.get("executable_top_of_book"))
    if observed:
        top = _num(quote.get("best_ask" if side == "LONG" else "best_bid"), reference_price)
        base = top if top > 0 else reference_price
        proxy_half_spread = 0.0
    else:
        base = _num(quote.get("mark_price"), reference_price) or reference_price
        proxy_half_spread = _execution_spread_bps(config) / 2.0
    adverse_bps = slippage_bps + proxy_half_spread
    if side == "LONG":
        fill = base * (1.0 + adverse_bps / 10000.0)
        slippage_vs_reference = ((fill / reference_price) - 1.0) * 10000.0 if reference_price > 0 else 0.0
    else:
        fill = base * (1.0 - adverse_bps / 10000.0)
        slippage_vs_reference = ((reference_price / fill) - 1.0) * 10000.0 if fill > 0 and reference_price > 0 else 0.0
    return float(fill), {
        "entry_quote_observed": observed,
        "entry_quote_source": quote.get("source"),
        "entry_spread_bps_observed": _num(quote.get("spread_bps"), 0.0),
        "entry_configured_slippage_bps": slippage_bps,
        "entry_adverse_slippage_vs_reference_bps": round(float(slippage_vs_reference), 4),
    }


def evaluate_frozen_symbol_decision(
    freeze_record: dict[str, Any],
    symbol: str,
    decision_time: datetime | pd.Timestamp | str,
    *,
    source_root: str | Path | None = None,
    paper_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = _utc(decision_time)
    if pd.isna(decision):
        raise ValueError("decision_time must be a valid UTC timestamp")
    entry_tf = str(freeze_record.get("entry_timeframe") or "1h")
    analysis_tf = str(freeze_record.get("analysis_timeframe") or "4h")
    warmup = int((paper_policy or {}).get("signal_warmup_days", 120))
    context = _closed_feature_context(
        source_root=str(source_root or freeze_record.get("source_root") or OHLCV_STORE_ROOT),
        symbol=symbol,
        entry_timeframe=entry_tf,
        analysis_timeframe=analysis_tf,
        decision_time=decision,
        warmup_days=warmup,
    )
    if not context.get("ok"):
        return {"ok": False, "symbol": symbol.upper(), "decision_time": decision.isoformat(), "reason": context.get("reason")}

    router_policy = _policy_payload(freeze_record, "market_state_router")
    adaptive_policy = _policy_payload(freeze_record, "adaptive_evidence")
    state = identify_market_state(
        {"features": context["state_features"], "htf_context": context["state_htf_context"]},
        symbol=symbol.upper(),
        analysis_timeframe=analysis_tf,
        policy=router_policy,
    )
    state_time = context.get("state_decision_time")
    state_age_hours = float((decision - state_time).total_seconds() / 3600.0) if state_time is not None and not pd.isna(state_time) else 1e9
    max_state_age = max(0.0, _num(adaptive_policy.get("max_state_age_hours"), 8.0))
    eligible_actions = {str(x) for x in (adaptive_policy.get("eligible_router_actions") or ["TRADE_CANDIDATE"])}
    require_direction = bool(adaptive_policy.get("require_direction_match", True))

    regime = classify_regime(context["strategy_features"])
    regime_v25 = classify_detailed_regime(context["strategy_features"])
    strategies = _strategy_map(freeze_record)
    executions = _execution_map(freeze_record)
    rows: list[dict[str, Any]] = []
    for family, frozen in strategies.items():
        payload = deepcopy(frozen.get("strategy_payload") or {})
        config = deepcopy((executions.get(family) or {}).get("config") or {})
        bundle = is_bundle_payload(payload)
        opinion = _bundle_opinion(context["strategy_features"], payload) if bundle else score_from_slot(context["strategy_features"], strategy_payload_to_slot(payload))
        side = str(getattr(opinion, "bias", "WAIT") or "WAIT").upper()
        if bool(config.get("reverse_signal", False)) and side in {"LONG", "SHORT"}:
            side = "SHORT" if side == "LONG" else "LONG"
        allowed = side in {"LONG", "SHORT"}
        if side == "LONG" and not bool(config.get("allow_long", True)):
            allowed = False
        if side == "SHORT" and not bool(config.get("allow_short", True)):
            allowed = False
        if allowed and not _matches_segment_filter(
            dict(config.get("segment_filter") or {}),
            side=side,
            regime=regime,
            regime_v25=regime_v25,
            strategy_payload=payload,
            bundle_mode_payload=bundle,
        ):
            allowed = False

        state_available = bool(state_time is not None and not pd.isna(state_time) and 0 <= state_age_hours <= max_state_age)
        family_match = str(state.preferred_strategy_family or "") == family
        action_match = str(state.router_action or "") in eligible_actions
        direction_match = str(state.route_direction or "").upper() == side if require_direction else True
        router_match = bool(allowed and state_available and family_match and action_match and direction_match)
        rows.append(
            {
                "symbol": symbol.upper(),
                "decision_time": decision.isoformat(),
                "research_family": family,
                "strategy_name": str(frozen.get("strategy_name") or payload.get("strategy_name") or "Strategy"),
                "strategy_sha256": frozen.get("strategy_sha256"),
                "benchmark_only": bool(frozen.get("benchmark_only", False) or payload.get("benchmark_only", False)),
                "strategy_payload": _json_clean(payload),
                "execution_config": _json_clean(config),
                "execution_config_sha256": (executions.get(family) or {}).get("config_sha256"),
                "entry_mode": str(config.get("entry_mode") or "next_open").lower(),
                "cooldown_bars": int(config.get("cooldown_bars") or 3),
                "max_hold_bars": int(config.get("max_hold_bars") or 288),
                "side": side,
                "score": round(_num(getattr(opinion, "score", 0.0)), 4),
                "threshold": round(_num(getattr(opinion, "threshold", 0.0)), 4),
                "signal_allowed": bool(allowed),
                "regime": regime,
                "regime_group": regime_v25.get("regime_group"),
                "regime_detail": regime_v25.get("regime_detail"),
                "state_available": state_available,
                "state_decision_time": None if state_time is None or pd.isna(state_time) else state_time.isoformat(),
                "state_age_hours": round(state_age_hours, 4),
                "market_state": state.lifecycle_state,
                "state_label": state.state_label,
                "router_action": state.router_action,
                "router_direction": state.route_direction,
                "preferred_strategy_family": state.preferred_strategy_family,
                "router_confidence": _num(state.confidence, 0.0),
                "router_risk_multiplier": _num(state.risk_multiplier, 0.0),
                "router_match": router_match,
                "reference_price": context["reference_price"],
                "features": _json_clean(context["strategy_features"]),
                "htf_context": _json_clean(context["strategy_htf_context"]),
                "opinion_note": str(getattr(opinion, "note", "") or ""),
            }
        )

    return {
        "ok": True,
        "symbol": symbol.upper(),
        "decision_time": decision.isoformat(),
        "reference_price": context["reference_price"],
        "market_state": {
            "state_decision_time": None if state_time is None or pd.isna(state_time) else state_time.isoformat(),
            "market_state": state.lifecycle_state,
            "state_label": state.state_label,
            "direction": state.direction,
            "trend_strength": state.trend_strength,
            "volatility_state": state.volatility_state,
            "htf_alignment": state.htf_alignment,
            "confidence": _num(state.confidence, 0.0),
            "preferred_strategy_family": state.preferred_strategy_family,
            "router_action": state.router_action,
            "router_direction": state.route_direction,
            "risk_multiplier": _num(state.risk_multiplier, 0.0),
        },
        "signals": rows,
    }


def materialize_candidate(
    signal: dict[str, Any],
    quote: dict[str, Any],
    *,
    signal_features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    side = str(signal.get("side") or "").upper()
    if side not in {"LONG", "SHORT"} or not bool(signal.get("signal_allowed", False)):
        return None
    reference = _num(signal.get("reference_price"), 0.0)
    if reference <= 0:
        return None
    config = dict(signal.get("execution_config") or {})
    entry_price, quote_meta = executable_entry_price(side, quote, reference, config)
    payload = add_exit_family_to_rule_params(dict(signal.get("strategy_payload") or {}))
    features = dict(signal_features or signal.get("features") or {})
    levels = build_trade_levels(
        features,
        side,
        expected_rr=payload.get("expected_rr"),
        rule_params=dict(payload.get("rule_params") or {}),
        entry_price=entry_price,
    )
    if not levels:
        return None
    risk_distance_pct = abs((float(levels["stop_loss"]) / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0
    return {
        **{k: v for k, v in signal.items() if k not in {"features", "htf_context"}},
        "signal_features": _json_clean(features),
        "entry_reference_price": reference,
        "paper_entry_price": float(entry_price),
        "risk_distance_pct": round(float(risk_distance_pct), 6),
        "trade_levels": _json_clean(levels),
        "quote": _json_clean(quote),
        **quote_meta,
    }
