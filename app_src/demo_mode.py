from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .engine import analyze_symbol
from .trend_lifecycle import attach_lifecycle_fit_to_analysis

DEMO_SYMBOLS = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]


def _timeframe_delta(timeframe: str) -> timedelta:
    tf = str(timeframe or "15m").lower().strip()
    if tf.endswith("m"):
        return timedelta(minutes=max(1, int(tf[:-1] or 15)))
    if tf.endswith("h"):
        return timedelta(hours=max(1, int(tf[:-1] or 1)))
    if tf.endswith("d"):
        return timedelta(days=max(1, int(tf[:-1] or 1)))
    return timedelta(minutes=15)


def _ohlcv_from_closes(symbol: str, timeframe: str, closes: list[float], base_volume: float = 1000.0) -> pd.DataFrame:
    step = _timeframe_delta(timeframe)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    previous = closes[0]
    for i, close in enumerate(closes):
        open_price = previous if i else close * 0.999
        spread = max(close * 0.0015, abs(close - open_price) * 0.7)
        high = max(open_price, close) + spread
        low = min(open_price, close) - spread
        volume = base_volume * (1.0 + 0.25 * math.sin(i / 8.0))
        if i > len(closes) - 20:
            volume *= 1.15
        rows.append(
            {
                "open_time": start + i * step,
                "open": round(open_price, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": round(volume, 4),
                "close_time": start + (i + 1) * step,
                "is_closed": True,
            }
        )
        previous = close
    df = pd.DataFrame(rows)
    return df


def _pattern_closes(pattern: str, n: int, start_price: float) -> list[float]:
    closes: list[float] = []
    for i in range(n):
        x = i / max(1, n - 1)
        if pattern == "trend_pullback":
            trend = start_price * (1.0 + 0.22 * x)
            wave = start_price * 0.012 * math.sin(i / 7.0)
            pullback = -start_price * 0.035 * max(0, (i - (n - 45)) / 45.0) if i > n - 45 else 0
            rebound = start_price * 0.018 * max(0, (i - (n - 12)) / 12.0) if i > n - 12 else 0
            close = trend + wave + pullback + rebound
        elif pattern == "range_chop":
            close = start_price * (1.0 + 0.035 * math.sin(i / 9.0) + 0.006 * math.sin(i / 2.7))
        elif pattern == "compression_breakout":
            if i < n - 55:
                close = start_price * (1.0 + 0.025 * math.sin(i / 12.0))
            elif i < n - 12:
                close = start_price * (1.015 + 0.006 * math.sin(i / 3.0))
            else:
                close = start_price * (1.015 + 0.004 * (i - (n - 12)) + 0.006 * math.sin(i / 2.0))
        else:
            close = start_price
        closes.append(float(close))
    return closes


def build_demo_candles(symbol: str, timeframe: str = "15m", limit: int = 400) -> pd.DataFrame:
    symbol = str(symbol or "ETHUSDT").upper()
    if symbol.startswith("BTC"):
        pattern, price, volume = "compression_breakout", 64000.0, 900.0
    elif symbol.startswith("SOL"):
        pattern, price, volume = "range_chop", 145.0, 1800.0
    else:
        pattern, price, volume = "trend_pullback", 3600.0, 1200.0
    return _ohlcv_from_closes(symbol, timeframe, _pattern_closes(pattern, int(limit), price), base_volume=volume)


def build_demo_htf_frames(symbol: str, main_timeframe: str) -> dict[str, pd.DataFrame]:
    # Synthetic HTF context is intentionally light; it exists so Codespaces/demo mode can
    # exercise the same scanner/lifecycle UI without a live collector or private OHLCV store.
    return {
        "1h": build_demo_candles(symbol, "1h", 300),
        "4h": build_demo_candles(symbol, "4h", 240),
    }


def build_demo_extras(symbol: str) -> dict[str, Any]:
    symbol = str(symbol or "").upper()
    if symbol.startswith("BTC"):
        return {"funding_rate": 0.00007, "open_interest": 1250000000, "order_book_imbalance": 0.12}
    if symbol.startswith("SOL"):
        return {"funding_rate": -0.00002, "open_interest": 420000000, "order_book_imbalance": -0.04}
    return {"funding_rate": 0.00003, "open_interest": 880000000, "order_book_imbalance": 0.08}


def run_demo_scanner(
    slot_rows: list[dict[str, Any]],
    analysis_timeframe: str = "15m",
    symbols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    scanner_rows: list[dict[str, Any]] = []
    analysis_map: dict[str, dict[str, Any]] = {}
    for symbol in symbols or DEMO_SYMBOLS:
        df = build_demo_candles(symbol, analysis_timeframe, 400)
        analysis = analyze_symbol(
            df,
            slot_rows,
            extras=build_demo_extras(symbol),
            htf_frames=build_demo_htf_frames(symbol, analysis_timeframe),
            bundle_payloads=[],
        )
        lifecycle = attach_lifecycle_fit_to_analysis(analysis, symbol=symbol, analysis_tf=analysis_timeframe)
        analysis_map[symbol] = analysis
        opinions = (analysis.get("strategies") or []) + (analysis.get("bundles") or [])
        directional = [x for x in opinions if isinstance(x, dict) and x.get("bias") in {"LONG", "SHORT"}]
        fit_ready = [x for x in directional if x.get("allowed_by_lifecycle")]
        scanner_rows.append(
            {
                "symbol": symbol,
                "last_open_time": analysis.get("features", {}).get("open_time"),
                "close": analysis.get("features", {}).get("close"),
                "regime": analysis.get("summary", {}).get("regime"),
                "final_bias": analysis.get("summary", {}).get("final_bias"),
                "final_score": analysis.get("summary", {}).get("final_score"),
                "recommendation": analysis.get("summary", {}).get("recommendation"),
                "htf_alignment": analysis.get("features", {}).get("htf_alignment"),
                "funding_rate": analysis.get("features", {}).get("funding_rate"),
                "oi": analysis.get("features", {}).get("open_interest"),
                "ob_imbalance": analysis.get("features", {}).get("order_book_imbalance"),
                "bundle_count": len(analysis.get("bundles", []) or []),
                "best_bundle": None,
                "demo_mode": True,
                "lifecycle_state": lifecycle.lifecycle_state,
                "lifecycle_direction": lifecycle.trend_direction,
                "lifecycle_confidence": lifecycle.confidence,
                "lifecycle_entry_mode": lifecycle.entry_mode,
                "lifecycle_exit_family": lifecycle.exit_family,
                "fit_ready_count": len(fit_ready),
                "directional_opinion_count": len(directional),
                "blocked_or_conflict_count": len([x for x in directional if x.get("fit_status") in {"blocked", "direction_conflict"}]),
                "best_fit_strategy": max(fit_ready, key=lambda x: float(x.get("score") or 0)).get("strategy_name") if fit_ready else None,
            }
        )
    return scanner_rows, analysis_map
