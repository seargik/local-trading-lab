from __future__ import annotations

"""V28.18 compatibility facade for the historical backtest engine.

The original engine is preserved verbatim in ``backtest_core_legacy.py``.  This
facade changes only the *availability clock* used while the historical engine
constructs feature rows: a candle becomes available at its close, not at its
open.  Price/volume features are still calculated from the original candle
timestamps, so session-dependent indicators keep their original semantics.

This is intentionally a timing-integrity hardening layer.  It does not change
strategy rules, exits, execution costs, or live/paper behavior.
"""

from contextlib import contextmanager
from threading import RLock
from typing import Any
import json

import pandas as pd

from . import backtest_core_legacy as _legacy
from .backtest_core_legacy import *  # noqa: F401,F403 - compatibility surface

TIMING_INTEGRITY_VERSION = "28.18"
_ORIGINAL_ENRICH_FEATURES = _legacy.enrich_features
_ORIGINAL_RUN_BACKTEST = _legacy.run_backtest
_ORIGINAL_RUN_BACKTEST_MATRIX = _legacy.run_backtest_matrix
_PATCH_LOCK = RLock()


def _infer_bar_delta(frame: pd.DataFrame) -> pd.Timedelta:
    """Infer a canonical candle duration from ordered open timestamps."""
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


def _closed_bar_enrich_features(frame: pd.DataFrame, *args: Any, **kwargs: Any):
    """Return normal features while timestamping each row at candle close.

    ``enrich_features`` itself runs first on untouched timestamps.  Only the
    returned feature-frame availability timestamp is shifted.  This prevents
    a 4h/1d feature row from being selected during the candle that creates it.
    """
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
    """Temporarily make legacy feature rows available only after candle close."""
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
            available = pd.to_datetime(ctx.get("open_time"), utc=True, errors="coerce")
            if pd.isna(available):
                continue
            checked += 1
            if available > signal_time:
                violations += 1
    return {"rows": int(checked), "violations": int(violations), "ok": bool(violations == 0)}


def audit_backtest_timing(result: Any) -> dict[str, Any]:
    """Audit the temporal contract of a completed backtest result."""
    trades = getattr(result, "trades", pd.DataFrame())
    if trades is None or trades.empty:
        return {
            "version": TIMING_INTEGRITY_VERSION,
            "trades": 0,
            "signal_after_entry_violations": 0,
            "htf_context": {"rows": 0, "violations": 0, "ok": True},
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
        "ok": bool(signal_after_entry == 0 and htf["ok"]),
    }


def run_backtest(*args: Any, **kwargs: Any):
    """Run the existing engine with a closed-bar-only feature availability clock."""
    with _closed_bar_feature_clock():
        result = _ORIGINAL_RUN_BACKTEST(*args, **kwargs)

    audit = audit_backtest_timing(result)
    result.config["timing_integrity_version"] = TIMING_INTEGRITY_VERSION
    result.config["closed_bar_only"] = True
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
