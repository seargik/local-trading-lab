from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .backtest_core import load_bootstrap_frame, resample_ohlcv, timeframe_minutes
from .features import build_htf_row, enrich_features, summarize_htf_context
from .market_state_v2816 import identify_market_state, load_market_state_policy
from .settings import HTF_MAP, OHLCV_STORE_ROOT

POLICY_PATH = Path("config/market_state_replay_policy.json")
SNAPSHOT_DIR = Path("data/backtest_reviews/market_state_replays")


@dataclass
class MarketStateReplayResult:
    config: dict[str, Any]
    history: pd.DataFrame
    state_summary: pd.DataFrame
    router_summary: pd.DataFrame
    transition_summary: pd.DataFrame
    dwell_summary: pd.DataFrame
    confidence_summary: pd.DataFrame
    no_lookahead_audit: dict[str, Any]
    conclusions: list[str]


def load_replay_policy(path: str | Path = POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("market-state replay policy must be a JSON object")
    return payload


def _delta(timeframe: str) -> pd.Timedelta:
    minutes = timeframe_minutes(timeframe)
    if minutes <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return pd.Timedelta(minutes=minutes)


def _closed_times(frame: pd.DataFrame, timeframe: str) -> np.ndarray:
    if frame.empty:
        return np.array([], dtype="datetime64[ns]")
    values = pd.to_datetime(frame["open_time"], utc=True, errors="coerce") + _delta(timeframe)
    return values.to_numpy(dtype="datetime64[ns]")


def _latest_closed_row(frame: pd.DataFrame, closed_times: np.ndarray, ts: pd.Timestamp) -> tuple[dict[str, Any] | None, pd.Timestamp | None]:
    if frame.empty or len(closed_times) == 0 or pd.isna(ts):
        return None, None
    idx = int(np.searchsorted(closed_times, ts.to_datetime64(), side="right") - 1)
    if idx < 0 or idx >= len(frame):
        return None, None
    return frame.iloc[idx].to_dict(), pd.to_datetime(closed_times[idx], utc=True, errors="coerce")


def _validate_timeframe(frame: pd.DataFrame, timeframe: str) -> None:
    if len(frame) < 3:
        return
    times = pd.to_datetime(frame["open_time"], utc=True, errors="coerce").sort_values()
    actual = times.diff().dropna().dt.total_seconds().median() / 60.0
    expected = timeframe_minutes(timeframe)
    if pd.notna(actual) and expected > 0 and abs(float(actual) - expected) > max(1.0, expected * 0.1):
        raise ValueError(f"Loaded data cadence is about {actual:.1f} minutes, expected {expected} for {timeframe}.")


def _higher_context(
    frames: dict[str, pd.DataFrame],
    closed: dict[str, np.ndarray],
    ts: pd.Timestamp,
) -> tuple[dict[str, Any], pd.Timestamp | None]:
    context: dict[str, Any] = {}
    latest_close: pd.Timestamp | None = None
    for timeframe, frame in frames.items():
        row, close_time = _latest_closed_row(frame, closed[timeframe], ts)
        if row is None:
            continue
        context[timeframe] = build_htf_row(row)
        if close_time is not None and (latest_close is None or close_time > latest_close):
            latest_close = close_time
    return context, latest_close


def _state_features(row: dict[str, Any], htf_context: dict[str, Any]) -> dict[str, Any]:
    features = dict(row)
    features.update(summarize_htf_context(htf_context))
    for timeframe, ctx in htf_context.items():
        for key, value in ctx.items():
            features[f"htf_{timeframe}_{key}"] = value
    return features


def _directional_return(direction: str, value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    direction = str(direction or "").upper()
    if direction == "LONG":
        return float(value)
    if direction == "SHORT":
        return float(-value)
    return None


def replay_symbol_history(
    symbol: str,
    *,
    source_root: str | Path = OHLCV_STORE_ROOT,
    start_date: str | None = None,
    end_date: str | None = None,
    policy: dict[str, Any] | None = None,
    router_policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    policy = dict(policy or load_replay_policy())
    router_policy = dict(router_policy or load_market_state_policy())
    timeframe = str(policy.get("analysis_timeframe") or "4h")
    warmup_days = int(policy.get("warmup_days", 90))
    horizons = sorted({max(1, int(x)) for x in (policy.get("forward_horizons_bars") or [1, 3, 6])})
    start_ts = pd.to_datetime(start_date, utc=True, errors="coerce") if start_date else None
    end_ts = pd.to_datetime(end_date, utc=True, errors="coerce") if end_date else None
    load_start = start_ts - pd.Timedelta(days=warmup_days) if start_ts is not None else None

    raw = load_bootstrap_frame(source_root, symbol.upper(), timeframe=timeframe, start=load_start, end=end_ts)
    _validate_timeframe(raw, timeframe)
    frame = enrich_features(raw.sort_values("open_time").reset_index(drop=True)).frame.sort_values("open_time").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame()

    higher_tfs = [tf for tf in HTF_MAP.get(timeframe, []) if timeframe_minutes(tf) > timeframe_minutes(timeframe)]
    higher_frames = {tf: enrich_features(resample_ohlcv(raw, tf)).frame.sort_values("open_time").reset_index(drop=True) for tf in higher_tfs}
    higher_closed = {tf: _closed_times(higher_frame, tf) for tf, higher_frame in higher_frames.items()}
    bar_delta = _delta(timeframe)
    min_index = min(max(200, int(policy.get("minimum_warmup_bars", 200))), max(0, len(frame) - 1))
    rows: list[dict[str, Any]] = []

    for i in range(min_index, len(frame)):
        row = frame.iloc[i].to_dict()
        open_time = pd.to_datetime(row.get("open_time"), utc=True, errors="coerce")
        if pd.isna(open_time):
            continue
        decision_time = open_time + bar_delta
        if start_ts is not None and decision_time < start_ts:
            continue
        if end_ts is not None and decision_time > end_ts:
            break
        htf_context, latest_htf_close = _higher_context(higher_frames, higher_closed, decision_time)
        features = _state_features(row, htf_context)
        state = identify_market_state(
            {"features": features, "htf_context": htf_context},
            symbol=symbol.upper(),
            analysis_timeframe=timeframe,
            policy=router_policy,
        )
        lookahead_ok = bool(latest_htf_close is None or latest_htf_close <= decision_time)
        record: dict[str, Any] = {
            "symbol": symbol.upper(),
            "bar_open_time": open_time,
            "decision_time": decision_time,
            "latest_htf_close_time": latest_htf_close,
            "lookahead_ok": lookahead_ok,
            "close": float(row.get("close")) if row.get("close") is not None else None,
            "market_state": state.lifecycle_state,
            "state_label": state.state_label,
            "direction": state.direction,
            "trend_strength": state.trend_strength,
            "volatility_state": state.volatility_state,
            "structure_state": state.structure_state,
            "htf_alignment": state.htf_alignment,
            "confidence": state.confidence,
            "preferred_strategy_family": state.preferred_strategy_family,
            "router_action": state.router_action,
            "router_direction": state.route_direction,
            "risk_multiplier": state.risk_multiplier,
            "entry_mode": state.entry_mode,
            "exit_family": state.exit_family,
            "reason": " | ".join(state.reason),
        }
        current_close = float(row.get("close") or 0.0)
        for horizon in horizons:
            future_index = i + horizon
            forward = None
            if current_close > 0 and future_index < len(frame):
                future_close = float(frame.iloc[future_index].get("close") or 0.0)
                forward = ((future_close / current_close) - 1.0) * 100.0 if future_close > 0 else None
            record[f"forward_return_{horizon}bar_pct"] = round(float(forward), 4) if forward is not None else None
            directional = _directional_return(state.route_direction, forward)
            record[f"router_directional_return_{horizon}bar_pct"] = round(float(directional), 4) if directional is not None else None
            record[f"router_direction_correct_{horizon}bar"] = bool(directional > 0) if directional is not None else None
        rows.append(record)
    return pd.DataFrame(rows)


def _state_summary(history: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    total = len(history)
    rows: list[dict[str, Any]] = []
    for state, part in history.groupby("market_state", dropna=False):
        row = {
            "market_state": state,
            "observations": int(len(part)),
            "share_pct": round(float(len(part) / total * 100.0), 2),
            "avg_confidence": round(float(pd.to_numeric(part["confidence"], errors="coerce").fillna(0).mean()), 2),
        }
        for horizon in horizons:
            values = pd.to_numeric(part[f"router_directional_return_{horizon}bar_pct"], errors="coerce")
            valid = values.dropna()
            row[f"avg_directional_return_{horizon}bar_pct"] = round(float(valid.mean()), 4) if len(valid) else None
            row[f"direction_accuracy_{horizon}bar_pct"] = round(float((valid > 0).mean() * 100.0), 2) if len(valid) else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values("observations", ascending=False).reset_index(drop=True)


def _router_summary(history: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for keys, part in history.groupby(["preferred_strategy_family", "router_action"], dropna=False):
        row = {
            "preferred_strategy_family": keys[0],
            "router_action": keys[1],
            "observations": int(len(part)),
            "avg_confidence": round(float(pd.to_numeric(part["confidence"], errors="coerce").fillna(0).mean()), 2),
        }
        for horizon in horizons:
            values = pd.to_numeric(part[f"router_directional_return_{horizon}bar_pct"], errors="coerce").dropna()
            row[f"avg_directional_return_{horizon}bar_pct"] = round(float(values.mean()), 4) if len(values) else None
            row[f"direction_accuracy_{horizon}bar_pct"] = round(float((values > 0).mean() * 100.0), 2) if len(values) else None
        rows.append(row)
    return pd.DataFrame(rows).sort_values("observations", ascending=False).reset_index(drop=True)


def _transition_summary(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for symbol, part in history.sort_values(["symbol", "decision_time"]).groupby("symbol"):
        states = part["market_state"].astype(str).tolist()
        for previous, current in zip(states, states[1:]):
            rows.append({"symbol": symbol, "from_state": previous, "to_state": current, "changed": previous != current})
    if not rows:
        return pd.DataFrame(columns=["symbol", "from_state", "to_state", "transitions", "changed"])
    frame = pd.DataFrame(rows)
    return frame.groupby(["symbol", "from_state", "to_state", "changed"], as_index=False).size().rename(columns={"size": "transitions"}).sort_values("transitions", ascending=False).reset_index(drop=True)


def _dwell_summary(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    dwell_rows: list[dict[str, Any]] = []
    for symbol, part in history.sort_values(["symbol", "decision_time"]).groupby("symbol"):
        previous = None
        length = 0
        for state in part["market_state"].astype(str).tolist():
            if previous is None or state == previous:
                length += 1
            else:
                dwell_rows.append({"symbol": symbol, "market_state": previous, "dwell_bars": length})
                length = 1
            previous = state
        if previous is not None:
            dwell_rows.append({"symbol": symbol, "market_state": previous, "dwell_bars": length})
    if not dwell_rows:
        return pd.DataFrame()
    frame = pd.DataFrame(dwell_rows)
    return frame.groupby("market_state", as_index=False).agg(
        episodes=("dwell_bars", "size"),
        avg_dwell_bars=("dwell_bars", "mean"),
        median_dwell_bars=("dwell_bars", "median"),
        max_dwell_bars=("dwell_bars", "max"),
    ).sort_values("episodes", ascending=False).reset_index(drop=True)


def _confidence_summary(history: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()
    work = history.copy()
    work["confidence_band"] = pd.cut(
        pd.to_numeric(work["confidence"], errors="coerce"),
        bins=[-0.001, 54.999, 69.999, 84.999, 100.001],
        labels=["<55", "55-69", "70-84", "85-100"],
        include_lowest=True,
    )
    rows: list[dict[str, Any]] = []
    for band, part in work.groupby("confidence_band", observed=False):
        row = {"confidence_band": str(band), "observations": int(len(part))}
        for horizon in horizons:
            values = pd.to_numeric(part[f"router_directional_return_{horizon}bar_pct"], errors="coerce").dropna()
            row[f"avg_directional_return_{horizon}bar_pct"] = round(float(values.mean()), 4) if len(values) else None
            row[f"direction_accuracy_{horizon}bar_pct"] = round(float((values > 0).mean() * 100.0), 2) if len(values) else None
        rows.append(row)
    return pd.DataFrame(rows)


def run_market_state_replay(
    *,
    source_root: str | Path = OHLCV_STORE_ROOT,
    symbols: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    policy: dict[str, Any] | None = None,
    router_policy: dict[str, Any] | None = None,
) -> MarketStateReplayResult:
    policy = dict(policy or load_replay_policy())
    router_policy = dict(router_policy or load_market_state_policy())
    symbols = [str(s).upper() for s in (symbols or policy.get("symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])]
    horizons = sorted({max(1, int(x)) for x in (policy.get("forward_horizons_bars") or [1, 3, 6])})
    frames = [
        replay_symbol_history(symbol, source_root=source_root, start_date=start_date, end_date=end_date, policy=policy, router_policy=router_policy)
        for symbol in symbols
    ]
    history = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in frames) else pd.DataFrame()
    state_summary = _state_summary(history, horizons)
    router_summary = _router_summary(history, horizons)
    transitions = _transition_summary(history)
    dwell = _dwell_summary(history)
    confidence = _confidence_summary(history, horizons)
    violations = int((~history["lookahead_ok"].astype(bool)).sum()) if not history.empty else 0
    changed = int(transitions.loc[transitions["changed"] == True, "transitions"].sum()) if not transitions.empty else 0
    total_transitions = int(transitions["transitions"].sum()) if not transitions.empty else 0
    churn = float(changed / total_transitions) if total_transitions else 0.0
    audit = {
        "observations": int(len(history)),
        "lookahead_violations": violations,
        "passed": violations == 0,
        "state_changes": changed,
        "state_change_rate": round(churn, 4),
        "closed_analysis_bar_decision": True,
        "closed_higher_timeframes_required": bool(policy.get("require_closed_higher_timeframes", True)),
    }
    conclusions: list[str] = []
    conclusions.append("Closed-bar timing audit passed." if violations == 0 else f"Timing audit failed with {violations} higher-timeframe lookahead rows.")
    min_obs = int(policy.get("minimum_state_observations", 20))
    weak_states = state_summary[state_summary["observations"] < min_obs]["market_state"].astype(str).tolist() if not state_summary.empty else []
    if weak_states:
        conclusions.append(f"States with fewer than {min_obs} observations should not be interpreted yet: {', '.join(weak_states)}.")
    warning = float(policy.get("state_churn_warning_rate", 0.25))
    if churn > warning:
        conclusions.append(f"State churn is high at {churn:.1%}; the identifier may be switching too frequently for a stable adaptive policy.")
    else:
        conclusions.append(f"State change rate is {churn:.1%}, below the configured {warning:.0%} churn warning level.")
    if not confidence.empty and horizons:
        h = horizons[-1]
        valid = confidence.dropna(subset=[f"direction_accuracy_{h}bar_pct"])
        if len(valid) >= 2:
            best = valid.sort_values(f"direction_accuracy_{h}bar_pct", ascending=False).iloc[0]
            conclusions.append(f"Best confidence band at the {h}-bar diagnostic horizon is {best['confidence_band']} with {best[f'direction_accuracy_{h}bar_pct']:.1f}% directional agreement; treat this as descriptive, not a promotion rule.")
    conclusions.append("V28.17 validates state recognition and adaptive routing behavior historically. It does not place or simulate orders; strategy PnL validation remains a separate evidence stage.")
    config = {
        **policy,
        "source_root": str(source_root),
        "symbols": symbols,
        "start_date": start_date,
        "end_date": end_date,
        "router_policy_version": str(router_policy.get("version") or ""),
    }
    return MarketStateReplayResult(config, history, state_summary, router_summary, transitions, dwell, confidence, audit, conclusions)


def save_market_state_replay(result: MarketStateReplayResult, root: str | Path = SNAPSHOT_DIR) -> dict[str, str]:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = root_path / f"{stamp}_v28_17_market_state_replay"
    paths: dict[str, str] = {}
    for name in ["history", "state_summary", "router_summary", "transition_summary", "dwell_summary", "confidence_summary"]:
        frame = getattr(result, name)
        path = Path(f"{prefix}_{name}.csv")
        frame.to_csv(path, index=False)
        paths[name] = str(path)
    summary_path = Path(f"{prefix}_summary.json")
    summary_path.write_text(json.dumps({"config": result.config, "no_lookahead_audit": result.no_lookahead_audit, "conclusions": result.conclusions}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    paths["summary"] = str(summary_path)
    return paths
