from __future__ import annotations

"""V28.18 timing-integrity facade for the historical backtest engine.

The pre-V28.18 engine is preserved verbatim in ``backtest_core_legacy.py``.
This facade keeps its public API but hardens historical feature availability:

* a candle becomes available at its close, never at its open;
* backward filling is disabled while historical features are built;
* centered swing pivots are replaced by causally confirmed pivots;
* matrix/what-if runs use the same timing contract;
* completed results carry an explicit timing-integrity marker and audit.

Strategy rules, exits, friction assumptions, paper trading and live trading are
not changed here.  This module only changes what information a historical
backtest is allowed to know at a given timestamp.
"""

from contextlib import contextmanager
from threading import RLock
from typing import Any
import json

import numpy as np
import pandas as pd

from . import backtest_core_legacy as _legacy
from . import features as _features
from .backtest_core_legacy import *  # noqa: F401,F403 - compatibility surface

TIMING_INTEGRITY_VERSION = "28.18"
_ORIGINAL_ENRICH_FEATURES = _legacy.enrich_features
_ORIGINAL_RUN_BACKTEST = _legacy.run_backtest
_ORIGINAL_RUN_BACKTEST_MATRIX = _legacy.run_backtest_matrix
_ORIGINAL_COMPUTE_MARKET_STRUCTURE = _features.compute_market_structure
_ORIGINAL_DATAFRAME_BFILL = pd.DataFrame.bfill
_PATCH_LOCK = RLock()


def _infer_bar_delta(frame: pd.DataFrame) -> pd.Timedelta:
    """Infer the canonical candle duration from ordered open timestamps."""
    if frame is None or len(frame) < 2 or "open_time" not in frame.columns:
        return pd.Timedelta(0)
    times = pd.to_datetime(frame["open_time"], utc=True, errors="coerce").dropna().sort_values()
    if len(times) < 2:
        return pd.Timedelta(0)
    seconds = times.diff().dropna().dt.total_seconds()
    seconds = seconds[seconds > 0]
    if seconds.empty:
        return pd.Timedelta(0)
    return pd.Timedelta(seconds=float(seconds.median()))


def _causal_market_structure(df: pd.DataFrame):
    """Confirm a five-bar swing only after the two bars to its right exist.

    The legacy implementation used ``rolling(..., center=True)`` and therefore
    labelled a pivot on the pivot bar itself even though two future bars were
    required to confirm it.  Here the same five-bar idea is delayed until the
    confirmation bar, so a historical decision never receives an unconfirmed
    future pivot.
    """
    if df is None or df.empty:
        empty = pd.Series(dtype=object)
        return empty, pd.Series(dtype=float), pd.Series(dtype=float)

    highs = pd.to_numeric(df["high"], errors="coerce").reset_index(drop=True)
    lows = pd.to_numeric(df["low"], errors="coerce").reset_index(drop=True)
    labels: list[str] = []
    pivot_highs: list[float] = []
    pivot_lows: list[float] = []
    last_high: list[float] = []
    last_low: list[float] = []

    for i in range(len(df)):
        candidate = i - 2
        if candidate >= 2:
            hi_window = highs.iloc[candidate - 2 : candidate + 3]
            lo_window = lows.iloc[candidate - 2 : candidate + 3]
            if len(hi_window) == 5 and pd.notna(highs.iloc[candidate]) and highs.iloc[candidate] >= hi_window.max():
                pivot_highs.append(float(highs.iloc[candidate]))
                pivot_highs = pivot_highs[-2:]
            if len(lo_window) == 5 and pd.notna(lows.iloc[candidate]) and lows.iloc[candidate] <= lo_window.min():
                pivot_lows.append(float(lows.iloc[candidate]))
                pivot_lows = pivot_lows[-2:]

        last_high.append(pivot_highs[-1] if pivot_highs else np.nan)
        last_low.append(pivot_lows[-1] if pivot_lows else np.nan)
        if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
            hh = pivot_highs[-1] > pivot_highs[-2]
            hl = pivot_lows[-1] > pivot_lows[-2]
            lh = pivot_highs[-1] < pivot_highs[-2]
            ll = pivot_lows[-1] < pivot_lows[-2]
            if hh and hl:
                labels.append("higher_high_higher_low")
            elif lh and ll:
                labels.append("lower_high_lower_low")
            elif hh and ll:
                labels.append("higher_high_lower_low")
            elif lh and hl:
                labels.append("lower_high_higher_low")
            else:
                labels.append("mixed")
        else:
            labels.append("mixed")

    return (
        pd.Series(labels, index=df.index),
        pd.Series(last_high, index=df.index, dtype=float),
        pd.Series(last_low, index=df.index, dtype=float),
    )


def _no_backward_fill(self: pd.DataFrame, *args: Any, **kwargs: Any):
    """Historical feature building must never copy a future value backward."""
    if bool(kwargs.get("inplace", False)):
        return None
    return self.copy()


@contextmanager
def _causal_feature_math():
    """Remove two explicit feature-level sources of historical lookahead."""
    with _PATCH_LOCK:
        previous_structure = _features.compute_market_structure
        previous_bfill = pd.DataFrame.bfill
        _features.compute_market_structure = _causal_market_structure
        pd.DataFrame.bfill = _no_backward_fill
        try:
            yield
        finally:
            _features.compute_market_structure = previous_structure
            pd.DataFrame.bfill = previous_bfill


def _closed_bar_enrich_features(frame: pd.DataFrame, *args: Any, **kwargs: Any):
    """Build causal features and timestamp each feature row at candle close.

    Indicators are calculated using the original candle timestamps so session
    features keep their intended clock semantics.  Only after feature creation
    is the availability timestamp shifted from open to close.
    """
    with _causal_feature_math():
        pack = _ORIGINAL_ENRICH_FEATURES(frame, *args, **kwargs)
    if pack.frame.empty or "open_time" not in pack.frame.columns:
        return pack
    delta = _infer_bar_delta(frame)
    if delta <= pd.Timedelta(0):
        return pack

    shifted = pack.frame.copy()
    source_open = pd.to_datetime(shifted["open_time"], utc=True, errors="coerce")
    shifted["source_open_time"] = source_open
    shifted["open_time"] = source_open + delta

    latest = dict(pack.latest or {})
    source_latest = pd.to_datetime(latest.get("open_time"), utc=True, errors="coerce")
    if not pd.isna(source_latest):
        latest["source_open_time"] = source_latest
        latest["open_time"] = source_latest + delta

    return type(pack)(frame=shifted, latest=latest)


@contextmanager
def _closed_bar_feature_clock():
    """Make legacy backtest feature rows available only after candle close."""
    with _PATCH_LOCK:
        previous = _legacy.enrich_features
        _legacy.enrich_features = _closed_bar_enrich_features
        try:
            yield
        finally:
            _legacy.enrich_features = previous


def _audit_htf_context(trades: pd.DataFrame) -> dict[str, Any]:
    if trades is None or trades.empty:
        return {"rows": 0, "violations": 0, "ok": True}
    violations = 0
    checked = 0
    for _, row in trades.iterrows():
        signal_time = pd.to_datetime(row.get("signal_time"), utc=True, errors="coerce")
        raw = row.get("htf_context_json")
        if pd.isna(signal_time) or not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        for ctx in payload.values():
            if not isinstance(ctx, dict):
                continue
            # V28.18 feature rows expose their availability time through
            # ``open_time`` and preserve the source candle open separately.
            available = pd.to_datetime(ctx.get("open_time"), utc=True, errors="coerce")
            if pd.isna(available):
                continue
            checked += 1
            if available > signal_time:
                violations += 1
    return {"rows": int(checked), "violations": int(violations), "ok": bool(violations == 0)}


def audit_backtest_timing(result: Any) -> dict[str, Any]:
    """Audit the temporal contract of a completed V28.18 backtest result."""
    trades = getattr(result, "trades", pd.DataFrame())
    if trades is None or trades.empty:
        return {
            "version": TIMING_INTEGRITY_VERSION,
            "trades": 0,
            "signal_after_entry_violations": 0,
            "htf_context": {"rows": 0, "violations": 0, "ok": True},
            "causal_pivots": True,
            "backward_fill_disabled": True,
            "ok": True,
        }
    signal = pd.to_datetime(trades.get("signal_time"), utc=True, errors="coerce")
    entry = pd.to_datetime(trades.get("entry_time"), utc=True, errors="coerce")
    signal_after_entry = int(((signal.notna()) & (entry.notna()) & (signal > entry)).sum())
    htf = _audit_htf_context(trades)
    return {
        "version": TIMING_INTEGRITY_VERSION,
        "trades": int(len(trades)),
        "signal_after_entry_violations": signal_after_entry,
        "htf_context": htf,
        "causal_pivots": True,
        "backward_fill_disabled": True,
        "ok": bool(signal_after_entry == 0 and htf["ok"]),
    }


def historical_enrich_features(frame: pd.DataFrame, *args: Any, **kwargs: Any):
    """Public helper for future historical/replay code that needs V28.18 causality."""
    return _closed_bar_enrich_features(frame, *args, **kwargs)


def run_backtest(*args: Any, **kwargs: Any):
    """Run the existing engine under the V28.18 closed-bar timing contract."""
    with _closed_bar_feature_clock():
        result = _ORIGINAL_RUN_BACKTEST(*args, **kwargs)

    audit = audit_backtest_timing(result)
    result.config["timing_integrity_version"] = TIMING_INTEGRITY_VERSION
    result.config["closed_bar_only"] = True
    result.config["causal_pivots"] = True
    result.config["backward_fill_disabled"] = True
    result.config["timing_integrity_audit"] = audit
    if result.trades is not None and not result.trades.empty:
        result.trades["timing_integrity_version"] = TIMING_INTEGRITY_VERSION
        result.trades["closed_bar_only"] = True
    return result


def run_backtest_matrix(*args: Any, **kwargs: Any):
    """Keep matrix/what-if runs on the same closed-bar timing contract."""
    with _PATCH_LOCK:
        previous = _legacy.run_backtest
        _legacy.run_backtest = run_backtest
        try:
            return _ORIGINAL_RUN_BACKTEST_MATRIX(*args, **kwargs)
        finally:
            _legacy.run_backtest = previous


def __getattr__(name: str):
    """Delegate private/legacy helpers so existing imports remain compatible."""
    return getattr(_legacy, name)
