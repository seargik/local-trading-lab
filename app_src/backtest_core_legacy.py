from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .engine import build_trade_levels
from .exit_families import add_exit_family_to_rule_params, infer_exit_family
from .ohlcv_store import load_range as load_range_from_store, standardize_ohlcv as standardize_store_ohlcv, resample_ohlcv as store_resample_ohlcv
from .features import build_htf_row, enrich_features, summarize_htf_context
from .regime_v25 import classify_detailed_regime
from .calibration_v25 import build_threshold_recommendations
from .settings import DEFAULT_SELECTED_SYMBOLS, HTF_MAP
from .strategies import StrategyOpinion, classify_regime, score_from_slot

BACKTESTS_DIR = Path("data/backtests")
PARQUET_CACHE_DIRNAME = "_parquet_cache"
TIMEFRAME_ORDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]
BINANCE_DEFAULT_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]


@dataclass
class BacktestResult:
    config: dict[str, Any]
    strategy_payload: dict[str, Any]
    summary: dict[str, Any]
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    performance_by_symbol: pd.DataFrame
    performance_by_period: pd.DataFrame
    performance_by_weekday: pd.DataFrame
    performance_by_hour: pd.DataFrame
    performance_by_day: pd.DataFrame
    performance_by_week: pd.DataFrame
    performance_by_month: pd.DataFrame
    performance_by_side: pd.DataFrame
    performance_by_regime: pd.DataFrame
    performance_by_side_regime: pd.DataFrame
    performance_by_exit_family: pd.DataFrame
    performance_by_score_side_decile: pd.DataFrame
    performance_by_score_decile: pd.DataFrame
    performance_by_detailed_regime: pd.DataFrame
    performance_by_side_detailed_regime: pd.DataFrame
    threshold_recommendations: pd.DataFrame
    performance_by_owner: pd.DataFrame
    bundle_validation: pd.DataFrame
    friction_comparison: pd.DataFrame
    counterfactuals: pd.DataFrame
    conclusions: list[str]
    suggestions: list[str]


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text or "").strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "backtest"


def timeframe_minutes(tf: str) -> int:
    tf = str(tf).strip().lower()
    mapping = {"m": 1, "h": 60, "d": 60 * 24, "w": 60 * 24 * 7}
    m = re.fullmatch(r"(\d+)([mhdw])", tf)
    if not m:
        return 0
    return int(m.group(1)) * mapping[m.group(2)]


def timeframe_to_pandas_rule(tf: str) -> str:
    tf = str(tf).strip().lower()
    m = re.fullmatch(r"(\d+)([mhdw])", tf)
    if not m:
        raise ValueError(f"Unsupported timeframe: {tf}")
    num, unit = m.groups()
    unit_map = {"m": "min", "h": "h", "d": "D", "w": "W"}
    return f"{int(num)}{unit_map[unit]}"


def merge_overrides(base_payload: dict[str, Any], override_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = deepcopy(base_payload)
    override_payload = override_payload or {}
    for key, value in override_payload.items():
        if key == "rule_params" and isinstance(value, dict):
            merged = dict(payload.get("rule_params") or {})
            merged.update(value)
            payload["rule_params"] = merged
        elif key == "indicator_rules" and isinstance(value, list):
            payload["indicator_rules"] = value
        else:
            payload[key] = value
    return payload


def strategy_payload_to_slot(payload: dict[str, Any], slot_id: int = 1) -> dict[str, Any]:
    payload = add_exit_family_to_rule_params(payload)
    return {
        "slot_id": slot_id,
        "strategy_id": int(payload.get("strategy_id") or 1),
        "version_id": int(payload.get("version_id") or 1),
        "version_no": int(payload.get("version_no") or 1),
        "strategy_name": payload.get("strategy_name") or "Strategy",
        "template_key": payload.get("template_key") or "rule_builder",
        "analyze": True,
        "enabled": True,
        "score_threshold": float(payload.get("score_threshold") or (payload.get("rule_params") or {}).get("score_threshold") or 70),
        "indicators": payload.get("indicators") or [],
        "indicator_rules": payload.get("indicator_rules") or [],
        "rule_params": payload.get("rule_params") or {},
        "expected_rr": payload.get("expected_rr") or "1:3",
        "exit_family": payload.get("exit_family") or (payload.get("rule_params") or {}).get("exit_family"),
    }


def is_bundle_payload(payload: dict[str, Any] | None) -> bool:
    payload = payload or {}
    return bool(payload.get("strategy_type") == "bundle" or payload.get("bundle_mode") or payload.get("mode") or payload.get("components"))


def _bundle_component_payload(component: dict[str, Any], idx: int) -> dict[str, Any]:
    base = deepcopy(component.get("strategy_payload") or component)
    if component.get("min_score") is not None:
        base = merge_overrides(base, {"score_threshold": float(component.get("min_score"))})
    base.setdefault("strategy_name", component.get("strategy_name") or f"Component {idx}")
    return base


def bundle_components(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, component in enumerate(payload.get("components") or [], start=1):
        out.append({
            "meta": dict(component),
            "payload": _bundle_component_payload(component, idx),
        })
    return out


def apply_override_to_bundle_payload(bundle_payload: dict[str, Any], override_payload: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(bundle_payload)
    payload.update({k: v for k, v in override_payload.items() if k not in {"rule_params", "score_threshold", "indicator_rules"}})
    comps = []
    for component in payload.get("components") or []:
        meta = dict(component)
        strategy_payload = _bundle_component_payload(meta, 1)
        strategy_payload = merge_overrides(strategy_payload, override_payload)
        if override_payload.get("score_threshold") is not None:
            meta["min_score"] = float(override_payload.get("score_threshold"))
        meta["strategy_payload"] = strategy_payload
        comps.append(meta)
    payload["components"] = comps
    return payload


def build_bundle_payload(strategy_payloads: list[dict[str, Any]], bundle_config: dict[str, Any] | None = None) -> dict[str, Any]:
    bundle_config = dict(bundle_config or {})
    bundle_name = str(bundle_config.get("bundle_name") or "Bundle Strategy")
    components = []
    for payload in strategy_payloads:
        components.append({
            "strategy_name": payload.get("strategy_name"),
            "min_score": float((payload.get("score_threshold") or (payload.get("rule_params") or {}).get("score_threshold") or bundle_config.get("component_min_score") or 70)),
            "weight": float((bundle_config.get("weights") or {}).get(payload.get("strategy_name"), 1.0)),
            "strategy_payload": deepcopy(payload),
        })
    payload = {
        "strategy_type": "bundle",
        "strategy_name": bundle_name,
        "bundle_name": bundle_name,
        "bundle_mode": str(bundle_config.get("bundle_mode") or bundle_config.get("mode") or ("all_pass" if len(components) <= 2 else "n_of_m")),
        "n_required": int(bundle_config.get("n_required") or max(1, len(components))),
        "bundle_threshold": float(bundle_config.get("bundle_threshold") or max(1.0, len(components))),
        "components": components,
        "score_threshold": float(bundle_config.get("score_threshold") or 70),
        "expected_rr": str(bundle_config.get("expected_rr") or strategy_payloads[0].get("expected_rr") if strategy_payloads else "1:3"),
        "rule_params": dict(bundle_config.get("rule_params") or {}),
        "notes": str(bundle_config.get("notes") or ""),
    }
    return add_exit_family_to_rule_params(payload)


def _bundle_opinion(features: dict[str, Any], payload: dict[str, Any]) -> StrategyOpinion:
    components = bundle_components(payload)
    if not components:
        return StrategyOpinion(payload.get("bundle_name") or "Bundle", -1, -1, 1, 1, "bundle", True, True, "WAIT", 0.0, float(payload.get("score_threshold") or 70), "No components configured")
    by_side: dict[str, list[tuple[dict[str, Any], StrategyOpinion]]] = {"LONG": [], "SHORT": []}
    notes = []
    for idx, component in enumerate(components, start=1):
        comp_payload = component["payload"]
        meta = component["meta"]
        slot = strategy_payload_to_slot(comp_payload, slot_id=idx)
        opinion = score_from_slot(features, slot)
        min_score = float(meta.get("min_score") or opinion.threshold or 70)
        notes.append(f"{comp_payload.get('strategy_name')}: {opinion.bias} {opinion.score:.1f}/{min_score:.1f}")
        if opinion.bias in {"LONG", "SHORT"} and float(opinion.score) >= min_score:
            by_side[opinion.bias].append((meta, opinion))
    mode = str(payload.get("bundle_mode") or payload.get("mode") or "n_of_m").strip().lower()
    n_required = int(payload.get("n_required") or max(1, len(components)))
    bundle_threshold = float(payload.get("bundle_threshold") or n_required)
    weights = payload.get("weights") or {}
    side = "WAIT"
    score = 0.0
    if mode == "all_pass":
        for candidate in ["LONG", "SHORT"]:
            if len(by_side[candidate]) == len(components) and len(components) > 0:
                side = candidate
                score = min(op.score for _, op in by_side[candidate])
                break
    elif mode == "weighted_consensus":
        weighted = {}
        for candidate in ["LONG", "SHORT"]:
            weighted[candidate] = sum(float(weights.get(meta.get("strategy_name"), meta.get("weight", 1.0))) * float(op.score) / 100.0 for meta, op in by_side[candidate])
        best_side = max(weighted, key=weighted.get)
        if weighted[best_side] >= bundle_threshold and weighted[best_side] > weighted[[s for s in weighted if s != best_side][0]]:
            side = best_side
            score = round(weighted[best_side] * 100.0 / max(sum(float(weights.get(meta.get("strategy_name"), meta.get("weight", 1.0))) for meta, _ in by_side[best_side]), 1.0), 2)
    else:
        long_count = len(by_side["LONG"])
        short_count = len(by_side["SHORT"])
        if long_count >= n_required and long_count > short_count:
            side = "LONG"
            score = float(np.mean([op.score for _, op in by_side["LONG"]]))
        elif short_count >= n_required and short_count > long_count:
            side = "SHORT"
            score = float(np.mean([op.score for _, op in by_side["SHORT"]]))
    if side == "WAIT":
        score = max([op.score for side_rows in by_side.values() for _, op in side_rows] or [0.0])
    return StrategyOpinion(
        strategy_name=payload.get("bundle_name") or payload.get("strategy_name") or "Bundle",
        strategy_id=-1,
        version_id=-1,
        version_no=1,
        slot_id=1,
        template_key="bundle",
        analyze=True,
        enabled=True,
        bias=side,
        score=float(score),
        threshold=float(payload.get("score_threshold") or 70),
        note=" | ".join(notes),
    )


def _infer_symbol_timeframe(path: Path, default_timeframe: str = "5m") -> tuple[str | None, str]:
    parts = [path.stem.upper(), path.name.upper()] + [p.upper() for p in path.parts[-6:]]
    symbol = None
    timeframe = default_timeframe
    for part in parts:
        if part.startswith("SYMBOL="):
            symbol = part.split("=", 1)[1].upper()
        if part.startswith("TIMEFRAME="):
            timeframe = part.split("=", 1)[1].lower()
    for part in parts:
        m = re.search(r"([A-Z0-9]{2,20}USDT)", part)
        if m:
            symbol = symbol or m.group(1)
            break
    if symbol is None:
        known = sorted(DEFAULT_SELECTED_SYMBOLS, key=len, reverse=True)
        for candidate in known:
            if candidate in path.name.upper():
                symbol = candidate
                break
    for part in parts:
        m = re.search(r"(^|[^0-9A-Z])(1M|3M|5M|15M|30M|1H|2H|4H|6H|8H|12H|1D|1W)([^0-9A-Z]|$)", part)
        if m:
            timeframe = m.group(2).lower()
            break
    return symbol, timeframe


def discover_bootstrap_files(source_root: str | Path, default_timeframe: str = "5m") -> list[dict[str, Any]]:
    root = Path(source_root)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".csv", ".parquet"}:
            continue
        if PARQUET_CACHE_DIRNAME in path.parts:
            continue
        symbol, timeframe = _infer_symbol_timeframe(path, default_timeframe=default_timeframe)
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "format": path.suffix.lower().lstrip("."),
            "path": str(path),
            "size_mb": round(path.stat().st_size / 1024 / 1024, 2),
        })
    return out


def _read_source_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)

    try:
        df = pd.read_csv(path)
        cols_lower = [str(c).strip().lower() for c in df.columns]
        if any(c in {"open_time", "timestamp", "date", "open time"} for c in cols_lower):
            df.columns = cols_lower
            return df
    except Exception:
        pass

    df = pd.read_csv(path, header=None)
    if df.shape[1] >= len(BINANCE_DEFAULT_COLUMNS):
        df = df.iloc[:, : len(BINANCE_DEFAULT_COLUMNS)]
        df.columns = BINANCE_DEFAULT_COLUMNS
    else:
        renamed = BINANCE_DEFAULT_COLUMNS[: df.shape[1]]
        df.columns = renamed
    return df


def standardize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename_map = {
        "open time": "open_time",
        "timestamp": "open_time",
        "date": "open_time",
        "Open time": "open_time",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "close time": "close_time",
        "Close time": "close_time",
    }
    frame = frame.rename(columns=rename_map)
    for required in ["open_time", "open", "high", "low", "close", "volume"]:
        if required not in frame.columns:
            raise ValueError(f"Missing required OHLCV column: {required}")

    if pd.api.types.is_numeric_dtype(frame["open_time"]):
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True, errors="coerce")
    else:
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    if "close_time" in frame.columns:
        if pd.api.types.is_numeric_dtype(frame["close_time"]):
            frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True, errors="coerce")
        else:
            frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True, errors="coerce")
    else:
        frame["close_time"] = frame["open_time"]

    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "is_closed" not in frame.columns:
        frame["is_closed"] = True
    frame["is_closed"] = frame["is_closed"].fillna(True).astype(bool)
    frame = frame[["open_time", "open", "high", "low", "close", "volume", "close_time", "is_closed"]].dropna(subset=["open_time", "open", "high", "low", "close"])
    frame = frame.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
    return frame


def _cache_dir_for(source_root: str | Path) -> Path:
    return Path(source_root) / PARQUET_CACHE_DIRNAME


def _cache_path_for(source_root: str | Path, symbol: str, timeframe: str) -> Path:
    return _cache_dir_for(source_root) / f"{symbol.upper()}__{timeframe}.parquet"


def load_bootstrap_frame(source_root: str | Path, symbol: str, timeframe: str = "5m", start: Any = None, end: Any = None) -> pd.DataFrame:
    symbol = symbol.upper()
    source_root = Path(source_root)
    start_ts = pd.to_datetime(start, utc=True, errors="coerce") if start is not None else None
    end_ts = pd.to_datetime(end, utc=True, errors="coerce") if end is not None else None

    # Fast path for partitioned parquet store.
    partition_root = source_root / f"symbol={symbol}" / f"timeframe={timeframe}"
    if partition_root.exists():
        frame = load_range_from_store(symbol, timeframe, start=start_ts, end=end_ts, store_root=source_root)
        if not frame.empty:
            return frame

    cache_path = _cache_path_for(source_root, symbol, timeframe)
    if cache_path.exists() and start_ts is None and end_ts is None:
        return standardize_ohlcv(pd.read_parquet(cache_path))

    files = [item for item in discover_bootstrap_files(source_root, default_timeframe=timeframe) if item["symbol"] == symbol and item["timeframe"] == timeframe]
    if not files:
        files = [item for item in discover_bootstrap_files(source_root, default_timeframe=timeframe) if item["symbol"] == symbol]
    if not files:
        raise FileNotFoundError(f"No bootstrap files found for {symbol} in {source_root}")

    frames: list[pd.DataFrame] = []
    for item in files:
        path = Path(item["path"])
        raw = _read_source_table(path)
        frames.append(standardize_ohlcv(raw))
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
    if start_ts is not None:
        out = out[out["open_time"] >= start_ts]
    if end_ts is not None:
        out = out[out["open_time"] <= end_ts]
    return out.reset_index(drop=True)


def convert_bootstrap_to_parquet(source_root: str | Path, default_timeframe: str = "5m") -> list[dict[str, Any]]:
    source_root = Path(source_root)
    cache_dir = _cache_dir_for(source_root)
    cache_dir.mkdir(parents=True, exist_ok=True)
    discovered = discover_bootstrap_files(source_root, default_timeframe=default_timeframe)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in discovered:
        grouped.setdefault((item["symbol"], item["timeframe"]), []).append(item)
    converted: list[dict[str, Any]] = []
    for (symbol, timeframe), items in grouped.items():
        frames = [standardize_ohlcv(_read_source_table(Path(item["path"]))) for item in items]
        merged = pd.concat(frames, ignore_index=True).sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
        target = _cache_path_for(source_root, symbol, timeframe)
        merged.to_parquet(target, index=False)
        converted.append({"symbol": symbol, "timeframe": timeframe, "rows": int(len(merged)), "target": str(target)})
    return converted


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    return store_resample_ohlcv(df, timeframe)


def _build_combined_features(entry_window: pd.DataFrame, analysis_window: pd.DataFrame, higher_context_frames: dict[str, pd.DataFrame], analysis_timeframe: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    entry_pack = enrich_features(entry_window)
    analysis_pack = enrich_features(analysis_window)
    if not entry_pack.latest or not analysis_pack.latest:
        return {}, {}, "No data"

    htf_context: dict[str, Any] = {analysis_timeframe: build_htf_row(analysis_pack.latest)}
    for tf, frame in higher_context_frames.items():
        if frame.empty:
            continue
        pack = enrich_features(frame)
        if pack.latest:
            htf_context[tf] = build_htf_row(pack.latest)

    features = dict(entry_pack.latest)
    features.update(summarize_htf_context(htf_context))
    for tf, ctx in htf_context.items():
        for key, value in ctx.items():
            features[f"htf_{tf}_{key}"] = value
    regime = classify_regime(features)
    return features, htf_context, regime


def _simulate_trade_path(
    future_df: pd.DataFrame,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    tp_levels: list[float] | None = None,
    late_trigger_index: int | None = None,
    be_trigger_index: int | None = None,
    lock_trigger_index: int | None = None,
    lock_to_tp_index: int | None = None,
) -> dict[str, Any]:
    df = future_df.copy().sort_values("open_time").reset_index(drop=True)
    levels = [float(x) for x in (tp_levels or [take_profit]) if x is not None]
    if not levels:
        levels = [float(take_profit)]
    late_trigger_index = max(1, min(int(late_trigger_index or len(levels)), len(levels)))
    be_trigger_index = max(1, min(int(be_trigger_index or 1), len(levels)))
    lock_trigger_index = max(be_trigger_index, min(int(lock_trigger_index or late_trigger_index), len(levels)))
    lock_to_tp_index = max(0, min(int(lock_to_tp_index or 1), len(levels)))

    def tp_hit(row: pd.Series, target: float) -> bool:
        return bool(row["high"] >= target) if side == "LONG" else bool(row["low"] <= target)

    def sl_hit(row: pd.Series, stop: float) -> bool:
        return bool(row["low"] <= stop) if side == "LONG" else bool(row["high"] >= stop)

    def pnl_pct(close_price: float) -> float:
        return ((close_price / entry_price) - 1.0) * (100 if side == "LONG" else -100)

    if side == "LONG":
        mfe_pct = ((df["high"].max() / entry_price) - 1.0) * 100
        mae_pct = ((df["low"].min() / entry_price) - 1.0) * 100
    else:
        mfe_pct = ((entry_price / df["low"].min()) - 1.0) * 100
        mae_pct = ((entry_price / df["high"].max()) - 1.0) * 100 * -1

    current_sl = float(stop_loss)
    sl_state = "INITIAL"
    highest_tp_hit = 0
    first_tp_offset: int | None = None
    first_tp_time = None
    tp_hits: dict[str, str] = {}
    close_price = float(df.iloc[-1]["close"])
    close_reason = "TIME_EXIT"
    outcome_label = "TIME_EXIT"
    status = "CLOSED"
    exit_offset = max(0, len(df) - 1)
    exit_time = df.iloc[-1]["open_time"]

    for idx, row in df.iterrows():
        candle_open = float(row.get("open") or row.get("close") or entry_price)
        next_tp = levels[highest_tp_hit] if highest_tp_hit < len(levels) else None
        hit_sl = sl_hit(row, current_sl)
        hit_tp = tp_hit(row, next_tp) if next_tp is not None else False
        if hit_sl and hit_tp and next_tp is not None:
            if abs(candle_open - current_sl) <= abs(candle_open - next_tp):
                hit_tp = False
            else:
                hit_sl = False

        if hit_tp and next_tp is not None:
            while highest_tp_hit < len(levels) and tp_hit(row, levels[highest_tp_hit]):
                highest_tp_hit += 1
                tp_hits[str(highest_tp_hit)] = str(row.get("open_time"))
                if highest_tp_hit == 1:
                    first_tp_offset = idx
                    first_tp_time = row.get("open_time")
                if highest_tp_hit >= be_trigger_index and sl_state == "INITIAL":
                    current_sl = float(entry_price)
                    sl_state = "BREAKEVEN"
                if highest_tp_hit >= lock_trigger_index and lock_to_tp_index > 0:
                    lock_idx = min(lock_to_tp_index, len(levels)) - 1
                    current_sl = float(levels[lock_idx])
                    sl_state = f"AT_TP{lock_idx + 1}"
                if highest_tp_hit >= len(levels):
                    close_reason = "TAKE_PROFIT_FINAL"
                    outcome_label = f"TP{len(levels)}_FINAL"
                    close_price = float(levels[-1])
                    exit_offset = idx
                    exit_time = row["open_time"]
                    break
            if close_reason == "TAKE_PROFIT_FINAL":
                break
            continue

        if hit_sl:
            if sl_state == "BREAKEVEN":
                close_reason = "STOP_LOSS_ENTRY"
                outcome_label = "SL_AT_ENTRY"
            elif sl_state == "AT_TP1":
                close_reason = "STOP_LOSS_TP1"
                outcome_label = "SL_AT_TP1"
            else:
                close_reason = "STOP_LOSS_INITIAL"
                outcome_label = "SL_INITIAL"
            close_price = float(current_sl)
            exit_offset = idx
            exit_time = row["open_time"]
            break

    follow_through_score = round(max(0.0, min(100.0, mfe_pct * 10 - abs(mae_pct) * 5 + highest_tp_hit * 6)), 2)
    return {
        "status": status,
        "close_reason": close_reason,
        "close_price": float(close_price),
        "outcome_label": outcome_label,
        "mfe_pct": round(float(mfe_pct), 4),
        "mae_pct": round(float(mae_pct), 4),
        "pnl_pct": round(float(pnl_pct(close_price)), 4),
        "follow_through_score": follow_through_score,
        "stop_loss_current": float(current_sl),
        "sl_state": sl_state,
        "highest_tp_hit": int(highest_tp_hit),
        "tp_hit_count": int(highest_tp_hit),
        "tp_hits_json": tp_hits,
        "first_tp_offset": int(first_tp_offset) if first_tp_offset is not None else None,
        "first_tp_time": first_tp_time,
        "close_time": exit_time,
        "exit_offset": int(exit_offset),
    }

def _safe_div(n: float, d: float) -> float:
    return float(n) / float(d) if d not in {0, 0.0, None} else 0.0


def _longest_streak(flags: list[bool], want: bool) -> int:
    best = cur = 0
    for flag in flags:
        if bool(flag) is want:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _tp1_hours(entry_time: pd.Timestamp, tp_hits_value: Any) -> float | None:
    try:
        if isinstance(tp_hits_value, str):
            tp_hits = json.loads(tp_hits_value) if tp_hits_value else {}
        elif isinstance(tp_hits_value, dict):
            tp_hits = tp_hits_value
        else:
            tp_hits = {}
        hit = tp_hits.get("1")
        if not hit:
            return None
        hit_ts = pd.to_datetime(hit, utc=True, errors="coerce")
        entry_ts = pd.to_datetime(entry_time, utc=True, errors="coerce")
        if pd.isna(hit_ts) or pd.isna(entry_ts):
            return None
        return float((hit_ts - entry_ts).total_seconds() / 3600.0)
    except Exception:
        return None


def _subset_summary_row(df: pd.DataFrame, label: str) -> dict[str, Any]:
    if df.empty:
        return {
            "scenario": label,
            "trades": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
        }
    gross_profit = df.loc[df["pnl_usd"] > 0, "pnl_usd"].sum()
    gross_loss = abs(df.loc[df["pnl_usd"] < 0, "pnl_usd"].sum())
    return {
        "scenario": label,
        "trades": int(len(df)),
        "win_rate": round(float(df["is_win"].mean() * 100), 2),
        "total_pnl_usd": round(float(df["pnl_usd"].sum()), 2),
        "expectancy_r": round(float(df["r_multiple"].fillna(0).mean()), 4),
        "profit_factor": round(float(_safe_div(gross_profit, gross_loss)), 4),
    }


def _score_decile_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    out = df[["score", "pnl_usd", "r_multiple", "is_win"]].copy()
    bucket_count = max(1, min(10, len(out)))
    if bucket_count == 1:
        out["score_decile"] = "D1"
    else:
        labels = [f"D{i}" for i in range(1, bucket_count + 1)]
        out["score_decile"] = pd.qcut(out["score"].rank(method="first"), q=bucket_count, labels=labels, duplicates="drop")
    grouped = out.groupby("score_decile", as_index=False).agg(
        trades=("score", "size"),
        avg_score=("score", "mean"),
        win_rate=("is_win", "mean"),
        total_pnl_usd=("pnl_usd", "sum"),
        avg_r=("r_multiple", "mean"),
    )
    grouped["win_rate"] = (grouped["win_rate"] * 100).round(2)
    grouped["avg_score"] = grouped["avg_score"].round(2)
    grouped["total_pnl_usd"] = grouped["total_pnl_usd"].round(2)
    grouped["avg_r"] = grouped["avg_r"].round(4)
    return grouped



def _performance_segment_frame(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Reusable segment analytics for side, regime, exit family, and side/regime views."""
    if df.empty or not group_cols:
        return pd.DataFrame()
    work = df.copy()
    for col in group_cols:
        if col not in work.columns:
            work[col] = "unknown"
        work[col] = work[col].fillna("unknown").astype(str)
    grouped = (
        work.groupby(group_cols, as_index=False)
        .agg(
            trades=("symbol", "size"),
            win_rate=("is_win", "mean"),
            total_pnl_usd=("pnl_usd", "sum"),
            total_pnl_pct=("pnl_pct", "sum"),
            avg_r=("r_multiple", "mean"),
            profit_trades=("is_win", "sum"),
            avg_score=("score", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
            tp1_hit_rate=("highest_tp_hit", lambda s: float((pd.to_numeric(s, errors="coerce").fillna(0) >= 1).mean())),
            final_tp_hit_rate=("outcome_label", lambda s: float(s.fillna("").astype(str).str.contains("FINAL").mean())),
        )
    )
    # Profit factor cannot be expressed as a simple mean aggregation.
    pf_rows = []
    for keys, part in work.groupby(group_cols, dropna=False):
        gross_profit = float(part.loc[part["pnl_usd"] > 0, "pnl_usd"].sum())
        gross_loss = abs(float(part.loc[part["pnl_usd"] < 0, "pnl_usd"].sum()))
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: keys[idx] for idx, col in enumerate(group_cols)}
        row["profit_factor"] = round(float(_safe_div(gross_profit, gross_loss)), 4)
        pf_rows.append(row)
    pf = pd.DataFrame(pf_rows)
    if not pf.empty:
        grouped = grouped.merge(pf, on=group_cols, how="left")
    grouped["win_rate"] = (grouped["win_rate"] * 100).round(2)
    grouped["tp1_hit_rate"] = (grouped["tp1_hit_rate"] * 100).round(2)
    grouped["final_tp_hit_rate"] = (grouped["final_tp_hit_rate"] * 100).round(2)
    for col in ["total_pnl_usd", "total_pnl_pct", "avg_score", "avg_mfe_pct", "avg_mae_pct"]:
        if col in grouped.columns:
            grouped[col] = grouped[col].round(4 if col.endswith("_pct") else 2)
    grouped["avg_r"] = grouped["avg_r"].round(4)
    return grouped.sort_values(["total_pnl_usd", "trades"], ascending=[False, False]).reset_index(drop=True)


def _score_side_decile_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    frames = []
    for side, part in df.groupby("side", dropna=False):
        frame = _score_decile_frame(part.copy())
        if not frame.empty:
            frame.insert(0, "side", side)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _owner_performance_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Summarise single-vs-bundle owners without merging their trade ownership."""
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    for col in ["strategy_mode", "trade_owner_key", "strategy_name", "side"]:
        if col not in work.columns:
            work[col] = "unknown"
        work[col] = work[col].fillna("unknown").astype(str)
    grouped = (
        work.groupby(["strategy_mode", "trade_owner_key", "strategy_name", "side"], as_index=False)
        .agg(
            trades=("symbol", "size"),
            win_rate=("is_win", "mean"),
            total_pnl_usd=("pnl_usd", "sum"),
            total_pnl_pct=("pnl_pct", "sum"),
            raw_pnl_pct=("raw_pnl_pct", "sum"),
            avg_execution_cost_pct=("execution_cost_pct", "mean"),
            avg_r=("r_multiple", "mean"),
            avg_score=("score", "mean"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
        )
    )
    pf_rows = []
    for keys, part in work.groupby(["strategy_mode", "trade_owner_key", "strategy_name", "side"], dropna=False):
        gross_profit = float(part.loc[part["pnl_usd"] > 0, "pnl_usd"].sum())
        gross_loss = abs(float(part.loc[part["pnl_usd"] < 0, "pnl_usd"].sum()))
        row = {"strategy_mode": keys[0], "trade_owner_key": keys[1], "strategy_name": keys[2], "side": keys[3]}
        row["profit_factor"] = round(float(_safe_div(gross_profit, gross_loss)), 4)
        pf_rows.append(row)
    pf = pd.DataFrame(pf_rows)
    if not pf.empty:
        grouped = grouped.merge(pf, on=["strategy_mode", "trade_owner_key", "strategy_name", "side"], how="left")
    grouped["win_rate"] = (grouped["win_rate"] * 100).round(2)
    for col in ["total_pnl_usd", "total_pnl_pct", "raw_pnl_pct", "avg_execution_cost_pct", "avg_score", "avg_mfe_pct", "avg_mae_pct"]:
        grouped[col] = grouped[col].round(4 if col.endswith("_pct") else 2)
    grouped["avg_r"] = grouped["avg_r"].round(4)
    return grouped.sort_values(["total_pnl_usd", "trades"], ascending=[False, False]).reset_index(drop=True)


def _bundle_validation_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Validate whether bundle-owned trades are producing cleaner results than single-owned trades in the same result set."""
    if df.empty or "strategy_mode" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["strategy_mode"] = work["strategy_mode"].fillna("single").astype(str)
    rows = []
    for mode, part in work.groupby("strategy_mode", dropna=False):
        gross_profit = float(part.loc[part["pnl_usd"] > 0, "pnl_usd"].sum())
        gross_loss = abs(float(part.loc[part["pnl_usd"] < 0, "pnl_usd"].sum()))
        rows.append({
            "strategy_mode": mode,
            "trades": int(len(part)),
            "win_rate": round(float(part["is_win"].mean() * 100), 2) if len(part) else 0.0,
            "total_pnl_usd": round(float(part["pnl_usd"].sum()), 2),
            "total_pnl_pct": round(float(part["pnl_pct"].sum()), 4),
            "raw_pnl_pct": round(float(part.get("raw_pnl_pct", pd.Series(dtype=float)).sum()), 4),
            "profit_factor": round(float(_safe_div(gross_profit, gross_loss)), 4),
            "avg_score": round(float(part["score"].mean()), 2) if "score" in part else 0.0,
            "avg_execution_cost_pct": round(float(part.get("execution_cost_pct", pd.Series(dtype=float)).mean()), 4) if "execution_cost_pct" in part else 0.0,
        })
    out = pd.DataFrame(rows).sort_values(["total_pnl_usd", "profit_factor"], ascending=[False, False]).reset_index(drop=True)
    if set(out["strategy_mode"].astype(str)) >= {"single", "bundle"}:
        single = out.loc[out["strategy_mode"] == "single"].iloc[0]
        bundle = out.loc[out["strategy_mode"] == "bundle"].iloc[0]
        out.loc[out["strategy_mode"] == "bundle", "delta_vs_single_pnl_usd"] = round(float(bundle["total_pnl_usd"] - single["total_pnl_usd"]), 2)
        out.loc[out["strategy_mode"] == "bundle", "delta_vs_single_pf"] = round(float(bundle["profit_factor"] - single["profit_factor"]), 4)
    return out


def _friction_comparison_frame(df: pd.DataFrame, fixed_stake_usd: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    raw_pct = pd.to_numeric(work.get("raw_pnl_pct", work.get("pnl_pct", 0.0)), errors="coerce").fillna(0.0)
    net_pct = pd.to_numeric(work.get("pnl_pct", 0.0), errors="coerce").fillna(0.0)
    cost_pct = pd.to_numeric(work.get("execution_cost_pct", raw_pct - net_pct), errors="coerce").fillna(0.0)
    rows = []
    for label, pct_series in [("gross_no_friction", raw_pct), ("net_current_friction", net_pct)]:
        pnl_usd = fixed_stake_usd * pct_series / 100.0
        gross_profit = float(pnl_usd[pnl_usd > 0].sum())
        gross_loss = abs(float(pnl_usd[pnl_usd < 0].sum()))
        rows.append({
            "scenario": label,
            "trades": int(len(work)),
            "win_rate": round(float((pct_series > 0.0000001).mean() * 100), 2),
            "total_pnl_usd": round(float(pnl_usd.sum()), 2),
            "total_pnl_pct": round(float(pct_series.sum()), 4),
            "profit_factor": round(float(_safe_div(gross_profit, gross_loss)), 4),
            "avg_trade_pct": round(float(pct_series.mean()), 4),
        })
    rows.append({
        "scenario": "friction_drag",
        "trades": int(len(work)),
        "win_rate": 0.0,
        "total_pnl_usd": round(float((fixed_stake_usd * cost_pct / 100.0).sum()), 2),
        "total_pnl_pct": round(float(cost_pct.sum()), 4),
        "profit_factor": 0.0,
        "avg_trade_pct": round(float(cost_pct.mean()), 4),
    })
    return pd.DataFrame(rows)

def _counterfactual_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    aligned_mask = ((df["side"] == "LONG") & (df["htf_alignment"] == "bullish")) | ((df["side"] == "SHORT") & (df["htf_alignment"] == "bearish"))
    top_quartile_cut = float(df["score"].quantile(0.75)) if len(df) >= 4 else float(df["score"].min())
    scenarios = [
        _subset_summary_row(df, "Baseline"),
        _subset_summary_row(df.loc[aligned_mask].copy(), "Only HTF aligned"),
        _subset_summary_row(df.loc[df["score"] >= top_quartile_cut].copy(), "Only top score quartile"),
        _subset_summary_row(df.loc[df["score"] >= 85].copy(), "Only score >= 85"),
    ]
    return pd.DataFrame(scenarios)


def build_what_if_tasks(strategy_payload: dict[str, Any], base_config: dict[str, Any] | None = None, matrix_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    base_config = dict(base_config or {})
    matrix_config = dict(matrix_config or {})
    if is_bundle_payload(strategy_payload):
        first_component = (strategy_payload.get("components") or [{}])[0]
        first_payload = _bundle_component_payload(first_component, 1) if first_component else {}
        base_threshold = float(first_component.get("min_score") or first_payload.get("score_threshold") or (first_payload.get("rule_params") or {}).get("score_threshold") or 70)
        override_merge = lambda ov: apply_override_to_bundle_payload(strategy_payload, ov)
    else:
        base_threshold = float(strategy_payload.get("score_threshold") or (strategy_payload.get("rule_params") or {}).get("score_threshold") or 70)
        override_merge = lambda ov: merge_overrides(strategy_payload, ov)
    stop_multipliers = matrix_config.get("stop_multipliers") or [1.25, 1.5, 2.0]
    tp_counts = matrix_config.get("tp_counts") or [2, 4]
    thresholds = matrix_config.get("score_thresholds") or sorted({max(35.0, base_threshold), 70.0, 85.0})
    include_baseline = bool(matrix_config.get("include_baseline", True))
    include_confirm_bar = bool(matrix_config.get("include_confirm_bar", True))
    include_reverse_signal = bool(matrix_config.get("include_reverse_signal", True))
    tasks: list[dict[str, Any]] = []
    if include_baseline:
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | Baseline",
            "scenario_name": "Baseline",
            "strategy_payload": deepcopy(strategy_payload),
            "config_overrides": {},
        })
    for mult in stop_multipliers:
        payload = override_merge({"rule_params": {"stop_multiplier": mult}})
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | Stop x{mult:g}",
            "scenario_name": f"Stop x{mult:g}",
            "strategy_payload": payload,
            "config_overrides": {},
        })
    for tp_count in tp_counts:
        payload = override_merge({"rule_params": {"tp_count": int(tp_count)}})
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | TP ladder {int(tp_count)}",
            "scenario_name": f"TP ladder {int(tp_count)}",
            "strategy_payload": payload,
            "config_overrides": {},
        })
    for threshold in sorted({float(x) for x in thresholds}):
        payload = override_merge({"score_threshold": threshold, "rule_params": {"score_threshold": threshold}})
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | Score >= {int(threshold)}",
            "scenario_name": f"Score >= {int(threshold)}",
            "strategy_payload": payload,
            "config_overrides": {},
        })
    if include_confirm_bar:
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | Confirm bar entry",
            "scenario_name": "Confirm bar entry",
            "strategy_payload": deepcopy(strategy_payload),
            "config_overrides": {"entry_mode": "confirm_bar"},
        })
    if include_reverse_signal:
        tasks.append({
            "name": f"{strategy_payload.get('strategy_name', 'Strategy')} | Reverse signal",
            "scenario_name": "Reverse signal",
            "strategy_payload": deepcopy(strategy_payload),
            "config_overrides": {"reverse_signal": True},
        })
    seen = set()
    deduped = []
    for task in tasks:
        key = (task.get('scenario_name'), json.dumps(task.get('strategy_payload', {}), sort_keys=True, default=str), json.dumps(task.get('config_overrides', {}), sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(task)
    return deduped


def run_backtest_matrix(
    source_root: str | Path,
    symbols: list[str],
    strategy_payload: dict[str, Any],
    entry_timeframe: str = "5m",
    analysis_timeframe: str = "1h",
    start_date: str | None = None,
    end_date: str | None = None,
    config: dict[str, Any] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    matrix_config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    base_config = dict(config or {})
    tasks = tasks or build_what_if_tasks(strategy_payload, base_config, matrix_config=matrix_config)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        scenario_name = task.get("scenario_name") or task.get("name") or "Scenario"
        scenario_payload = task.get("strategy_payload") or strategy_payload
        scenario_config = dict(base_config)
        scenario_config.update(task.get("config_overrides") or {})
        result = run_backtest(
            source_root=source_root,
            symbols=symbols,
            strategy_payload=scenario_payload,
            entry_timeframe=entry_timeframe,
            analysis_timeframe=analysis_timeframe,
            start_date=start_date,
            end_date=end_date,
            config=scenario_config,
        )
        summary = result.summary
        rows.append({
            "scenario": scenario_name,
            "trades": int(summary.get("total_trades", 0)),
            "win_rate": float(summary.get("win_rate", 0.0)),
            "breakeven_rate": float(summary.get("breakeven_rate", 0.0)),
            "total_pnl_usd": float(summary.get("total_pnl_usd", 0.0)),
            "total_pnl_pct": float(summary.get("total_pnl_pct", 0.0)),
            "pre_friction_pnl_usd": float(summary.get("pre_friction_pnl_usd", 0.0)),
            "total_execution_cost_usd": float(summary.get("total_execution_cost_usd", 0.0)),
            "expectancy_r": float(summary.get("expectancy_r", 0.0)),
            "profit_factor": float(summary.get("profit_factor", 0.0)),
            "payoff_ratio": float(summary.get("payoff_ratio", 0.0)),
            "max_drawdown_usd": float(summary.get("max_drawdown_usd", 0.0)),
            "tp1_hit_rate": float(summary.get("tp1_hit_rate", 0.0)),
            "final_tp_hit_rate": float(summary.get("final_tp_hit_rate", 0.0)),
            "avg_score": float(summary.get("avg_score", 0.0)),
            "avg_duration_h": float(summary.get("avg_trade_duration_hours", 0.0)),
            "entry_mode": str(scenario_config.get("entry_mode", "next_open")),
            "stop_multiplier": float((scenario_payload.get("rule_params") or {}).get("stop_multiplier", 1.0) or 1.0),
            "tp_count": int((scenario_payload.get("rule_params") or {}).get("tp_count", 0) or 0),
            "score_threshold": float(scenario_payload.get("score_threshold") or (scenario_payload.get("rule_params") or {}).get("score_threshold") or 0.0),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["total_pnl_usd", "expectancy_r", "profit_factor"], ascending=[False, False, False]).reset_index(drop=True)


def build_backtest_summary(trades: pd.DataFrame, fixed_stake_usd: float) -> tuple[Any, ...]:
    if trades.empty:
        summary = {
            "total_trades": 0,
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "breakeven_rate": 0.0,
            "partial_profit_rate": 0.0,
            "tp1_hit_rate": 0.0,
            "tp2_hit_rate": 0.0,
            "tp3_hit_rate": 0.0,
            "final_tp_hit_rate": 0.0,
            "expectancy_r": 0.0,
            "profit_factor": 0.0,
            "payoff_ratio": 0.0,
            "max_drawdown_usd": 0.0,
            "total_pnl_usd": 0.0,
            "total_pnl_pct": 0.0,
            "avg_trade_duration_hours": 0.0,
            "median_trade_duration_hours": 0.0,
            "avg_mfe_pct": 0.0,
            "avg_mae_pct": 0.0,
            "avg_score": 0.0,
            "median_score": 0.0,
            "median_pnl_pct_per_trade": 0.0,
            "median_pnl_usd_per_trade": 0.0,
            "avg_r_multiple": 0.0,
            "longest_win_streak": 0,
            "longest_loss_streak": 0,
            "avg_score_winners": 0.0,
            "avg_score_losers": 0.0,
            "breakeven_saves_count": 0,
            "stopped_at_tp1_lock_count": 0,
            "avg_realized_fraction_full_target": 0.0,
            "edge_partial_vs_full_usd": 0.0,
            "avg_mfe_before_stop_pct": 0.0,
            "avg_mae_before_tp_pct": 0.0,
            "avg_mfe_r": 0.0,
            "avg_mae_r": 0.0,
            "avg_time_to_tp1_hours": 0.0,
            "avg_slippage_proxy_pct": 0.0,
            "stop_efficiency_pct": 0.0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "pre_friction_pnl_pct": 0.0,
            "pre_friction_pnl_usd": 0.0,
            "total_execution_cost_pct": 0.0,
            "total_execution_cost_usd": 0.0,
            "avg_execution_cost_pct": 0.0,
        }
        empty = pd.DataFrame()
        # Keep the empty-return shape identical to the non-empty branch so callers can always unpack safely.
        return summary, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, empty, ["No trades were generated in this window."], ["Widen the date range, lower the score threshold, or test another symbol/strategy."]

    df = trades.copy().sort_values(["exit_time", "entry_time"]).reset_index(drop=True)
    df["is_win"] = df["pnl_pct"] > 0.0000001
    df["is_loss"] = df["pnl_pct"] < -0.0000001
    df["is_be"] = (~df["is_win"]) & (~df["is_loss"])
    df["r_multiple"] = df["pnl_pct"] / df["risk_pct"].replace(0, np.nan)
    df["pnl_usd"] = fixed_stake_usd * df["pnl_pct"] / 100.0
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True, errors="coerce")
    df["first_tp_time"] = pd.to_datetime(df.get("first_tp_time"), utc=True, errors="coerce")
    df["trade_duration_hours"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 3600.0
    df["time_to_tp1_hours"] = df.apply(lambda row: _tp1_hours(row.get("entry_time"), row.get("tp_hits_json")), axis=1)
    exit_time_naive = df["exit_time"].dt.tz_localize(None) if getattr(df["exit_time"].dt, "tz", None) is not None else df["exit_time"]
    df["period_month"] = exit_time_naive.dt.to_period("M").astype(str)
    df["period_week"] = exit_time_naive.dt.to_period("W").astype(str)
    df["period_day"] = df["exit_time"].dt.strftime("%Y-%m-%d")
    df["weekday"] = df["entry_time"].dt.day_name()
    df["entry_hour"] = df["entry_time"].dt.hour
    df["highest_tp_bucket"] = df["highest_tp_hit"].fillna(0).astype(int).map(lambda x: f"TP{x}")
    df["realized_fraction_full_target"] = (df["pnl_pct"].abs() / df["reward_pct"].replace(0, np.nan).abs()).clip(lower=0, upper=1.5)
    df["mfe_r"] = df["mfe_pct"] / df["risk_pct"].replace(0, np.nan)
    df["mae_r"] = df["mae_pct"] / df["risk_pct"].replace(0, np.nan)

    total = len(df)
    gross_profit = df.loc[df["pnl_usd"] > 0, "pnl_usd"].sum()
    gross_loss = abs(df.loc[df["pnl_usd"] < 0, "pnl_usd"].sum())
    avg_win = df.loc[df["is_win"], "pnl_usd"].mean() if df["is_win"].any() else 0.0
    avg_loss = abs(df.loc[df["is_loss"], "pnl_usd"].mean()) if df["is_loss"].any() else 0.0

    equity = df[["exit_time", "pnl_usd"]].copy().sort_values("exit_time").reset_index(drop=True)
    equity["trade_number"] = np.arange(1, len(equity) + 1)
    equity["cum_pnl_usd"] = equity["pnl_usd"].cumsum()
    equity["equity_peak_usd"] = equity["cum_pnl_usd"].cummax()
    equity["drawdown_usd"] = equity["cum_pnl_usd"] - equity["equity_peak_usd"]

    by_symbol = (
        df.groupby("symbol", as_index=False)
        .agg(
            trades=("symbol", "size"),
            win_rate=("is_win", "mean"),
            total_pnl_usd=("pnl_usd", "sum"),
            total_pnl_pct=("pnl_pct", "sum"),
            avg_r=("r_multiple", "mean"),
            avg_score=("score", "mean"),
        )
        .sort_values(["total_pnl_usd", "trades"], ascending=[False, False])
    )
    by_symbol["win_rate"] = (by_symbol["win_rate"] * 100).round(2)
    by_symbol["avg_r"] = by_symbol["avg_r"].round(4)
    by_symbol["avg_score"] = by_symbol["avg_score"].round(2)
    by_period_month = df.groupby("period_month", as_index=False).agg(trades=("symbol", "size"), total_pnl_usd=("pnl_usd", "sum"), win_rate=("is_win", "mean"))
    by_period_month["win_rate"] = (by_period_month["win_rate"] * 100).round(2)
    by_period_month = by_period_month.rename(columns={"period_month": "period"})
    by_period_week = df.groupby("period_week", as_index=False).agg(trades=("symbol", "size"), total_pnl_usd=("pnl_usd", "sum"), win_rate=("is_win", "mean"))
    by_period_week["win_rate"] = (by_period_week["win_rate"] * 100).round(2)
    by_period_week = by_period_week.rename(columns={"period_week": "period"})
    by_period_day = df.groupby("period_day", as_index=False).agg(trades=("symbol", "size"), total_pnl_usd=("pnl_usd", "sum"), win_rate=("is_win", "mean"))
    by_period_day["win_rate"] = (by_period_day["win_rate"] * 100).round(2)
    by_period_day = by_period_day.rename(columns={"period_day": "period"})
    by_weekday = df.groupby("weekday", as_index=False).agg(trades=("symbol", "size"), total_pnl_usd=("pnl_usd", "sum"), win_rate=("is_win", "mean"))
    by_weekday["win_rate"] = (by_weekday["win_rate"] * 100).round(2)
    by_hour = df.groupby("entry_hour", as_index=False).agg(trades=("symbol", "size"), total_pnl_usd=("pnl_usd", "sum"), win_rate=("is_win", "mean"))
    by_hour["win_rate"] = (by_hour["win_rate"] * 100).round(2)
    by_score_decile = _score_decile_frame(df)
    by_score_side_decile = _score_side_decile_frame(df)
    by_side = _performance_segment_frame(df, ["side"])
    by_regime = _performance_segment_frame(df, ["regime"])
    by_side_regime = _performance_segment_frame(df, ["side", "regime"])
    by_exit_family = _performance_segment_frame(df, ["exit_family"])
    by_detailed_regime = _performance_segment_frame(df, ["regime_group", "regime_detail"])
    by_side_detailed_regime = _performance_segment_frame(df, ["side", "regime_group", "regime_detail"])
    threshold_recommendations = build_threshold_recommendations(df)
    counterfactuals = _counterfactual_frame(df)

    summary = {
        "total_trades": int(total),
        "win_rate": round(float(df["is_win"].mean() * 100), 2),
        "loss_rate": round(float(df["is_loss"].mean() * 100), 2),
        "breakeven_rate": round(float(df["is_be"].mean() * 100), 2),
        "partial_profit_rate": round(float(((df["highest_tp_hit"] >= 1) & (~df["outcome_label"].fillna("").str.contains("FINAL"))).mean() * 100), 2),
        "tp1_hit_rate": round(float((df["highest_tp_hit"] >= 1).mean() * 100), 2),
        "tp2_hit_rate": round(float((df["highest_tp_hit"] >= 2).mean() * 100), 2),
        "tp3_hit_rate": round(float((df["highest_tp_hit"] >= 3).mean() * 100), 2),
        "final_tp_hit_rate": round(float(df["outcome_label"].fillna("").str.contains("FINAL").mean() * 100), 2),
        "expectancy_r": round(float(df["r_multiple"].fillna(0.0).mean()), 4),
        "avg_r_multiple": round(float(df["r_multiple"].fillna(0.0).mean()), 4),
        "profit_factor": round(float(_safe_div(gross_profit, gross_loss)), 4),
        "payoff_ratio": round(float(_safe_div(avg_win, avg_loss)), 4),
        "max_drawdown_usd": round(float(abs(equity["drawdown_usd"].min())), 2),
        "total_pnl_usd": round(float(df["pnl_usd"].sum()), 2),
        "total_pnl_pct": round(float(df["pnl_pct"].sum()), 4),
        "median_pnl_pct_per_trade": round(float(df["pnl_pct"].median()), 4),
        "median_pnl_usd_per_trade": round(float(df["pnl_usd"].median()), 4),
        "avg_trade_duration_hours": round(float(df["trade_duration_hours"].mean()), 2),
        "median_trade_duration_hours": round(float(df["trade_duration_hours"].median()), 2),
        "avg_time_to_tp1_hours": round(float(df["time_to_tp1_hours"].dropna().mean()) if df["time_to_tp1_hours"].notna().any() else 0.0, 2),
        "avg_mfe_pct": round(float(df["mfe_pct"].mean()), 4),
        "avg_mae_pct": round(float(df["mae_pct"].mean()), 4),
        "avg_mfe_before_stop_pct": round(float(df.loc[df["is_loss"], "mfe_pct"].mean() if df["is_loss"].any() else 0.0), 4),
        "avg_mae_before_tp_pct": round(float(df.loc[df["is_win"], "mae_pct"].mean() if df["is_win"].any() else 0.0), 4),
        "avg_mfe_r": round(float(df["mfe_r"].replace([np.inf, -np.inf], np.nan).mean()), 4),
        "avg_mae_r": round(float(df["mae_r"].replace([np.inf, -np.inf], np.nan).mean()), 4),
        "avg_score": round(float(df["score"].mean()), 2),
        "median_score": round(float(df["score"].median()), 2),
        "avg_score_winners": round(float(df.loc[df["is_win"], "score"].mean() if df["is_win"].any() else 0.0), 2),
        "avg_score_losers": round(float(df.loc[df["is_loss"], "score"].mean() if df["is_loss"].any() else 0.0), 2),
        "longest_win_streak": int(_longest_streak(df["is_win"].tolist(), True)),
        "longest_loss_streak": int(_longest_streak(df["is_loss"].tolist(), True)),
        "stake_per_trade_usd": float(fixed_stake_usd),
        "breakeven_saves_count": int((df["outcome_label"] == "SL_AT_ENTRY").sum()),
        "stopped_at_tp1_lock_count": int((df["outcome_label"] == "SL_AT_TP1").sum()),
        "avg_realized_fraction_full_target": round(float(df["realized_fraction_full_target"].fillna(0).mean()), 4),
        "edge_partial_vs_full_usd": round(float(df.loc[~df["outcome_label"].fillna("").str.contains("FINAL"), "pnl_usd"].sum() - df.loc[df["outcome_label"].fillna("").str.contains("FINAL"), "pnl_usd"].sum()), 2),
        "avg_slippage_proxy_pct": round(float(df.get("slippage_proxy_pct", pd.Series(dtype=float)).mean() if "slippage_proxy_pct" in df.columns else 0.0), 4),
        "stop_efficiency_pct": round(float((df.loc[df["is_loss"], "mae_pct"].abs() <= (df.loc[df["is_loss"], "risk_pct"].abs() * 1.1)).mean() * 100) if df["is_loss"].any() else 0.0, 2),
        "avg_win_usd": round(float(avg_win), 2),
        "avg_loss_usd": round(float(avg_loss), 2),
        "pre_friction_pnl_pct": round(float(df.get("raw_pnl_pct", df["pnl_pct"]).sum()), 4),
        "pre_friction_pnl_usd": round(float((fixed_stake_usd * df.get("raw_pnl_pct", df["pnl_pct"]) / 100.0).sum()), 2),
        "total_execution_cost_pct": round(float(df.get("execution_cost_pct", pd.Series(dtype=float)).sum() if "execution_cost_pct" in df.columns else 0.0), 4),
        "total_execution_cost_usd": round(float((fixed_stake_usd * df.get("execution_cost_pct", pd.Series(dtype=float)) / 100.0).sum() if "execution_cost_pct" in df.columns else 0.0), 2),
        "avg_execution_cost_pct": round(float(df.get("execution_cost_pct", pd.Series(dtype=float)).mean() if "execution_cost_pct" in df.columns else 0.0), 4),
    }

    by_owner = _owner_performance_frame(df)
    bundle_validation = _bundle_validation_frame(df)
    friction_comparison = _friction_comparison_frame(df, fixed_stake_usd)

    conclusions: list[str] = []
    suggestions: list[str] = []
    if total < 20:
        conclusions.append("Sample size is still small, so treat the result as directional rather than proven.")
    if summary["expectancy_r"] > 0 and summary["profit_factor"] > 1.1:
        conclusions.append("The strategy shows positive expectancy in this window.")
    else:
        conclusions.append("The strategy is not yet showing strong positive expectancy in this window.")
    if summary["tp1_hit_rate"] > summary["final_tp_hit_rate"] + 25:
        conclusions.append("A lot of trades reach early TP levels but fail to finish the move.")
        suggestions.append("Test a closer final TP, more partial exits, or a stronger trend filter.")
    if summary["avg_mae_pct"] < 0 and abs(summary["avg_mae_pct"]) > max(summary["avg_mfe_pct"], 0):
        conclusions.append("Average adverse excursion is larger than favorable excursion, which often means entries are early or noisy.")
        suggestions.append("Raise the score threshold or wait for a reclaim / confirmation style entry.")
    if summary["win_rate"] < 40:
        suggestions.append("Try fewer symbols or only the best-performing weekday/hour blocks instead of trading the whole set.")
    if summary["max_drawdown_usd"] > abs(summary["total_pnl_usd"]) and summary["total_pnl_usd"] > 0:
        suggestions.append("The path is rough relative to the outcome. Consider a stricter regime gate or lower trade frequency.")
    aligned_row = counterfactuals.loc[counterfactuals["scenario"] == "Only HTF aligned"]
    if not aligned_row.empty and float(aligned_row.iloc[0]["total_pnl_usd"]) > float(counterfactuals.iloc[0]["total_pnl_usd"]):
        suggestions.append("HTF alignment improves the result in this sample. Consider gating signals to aligned setups only.")
    if by_symbol.shape[0] > 0:
        best_symbol = by_symbol.iloc[0]["symbol"]
        worst_symbol = by_symbol.iloc[-1]["symbol"]
        conclusions.append(f"Best symbol in this run: {best_symbol}. Weakest symbol: {worst_symbol}.")
        suggestions.append("Compare trading only the top symbols versus the full basket.")
    if not suggestions:
        suggestions.append("Rerun with a JSON override on score_threshold, expected_rr, or rule_params to compare sensitivity.")

    return summary, equity, by_symbol, by_period_month, by_weekday, by_hour, by_period_day, by_period_week, by_period_month.copy(), by_side, by_regime, by_side_regime, by_exit_family, by_score_side_decile, by_score_decile, by_detailed_regime, by_side_detailed_regime, threshold_recommendations, by_owner, bundle_validation, friction_comparison, counterfactuals, conclusions[:6], suggestions[:6]


def _filter_match(value: Any, desired: Any) -> bool:
    if desired is None:
        return True
    if isinstance(desired, str):
        desired_text = desired.strip()
        if not desired_text or desired_text.lower() in {"all", "any", "*"}:
            return True
        desired_values = [desired_text]
    elif isinstance(desired, (list, tuple, set)):
        desired_values = [str(x).strip() for x in desired if str(x).strip()]
        if not desired_values:
            return True
    else:
        desired_values = [str(desired).strip()]
    actual = "unknown" if value is None else str(value).strip()
    if not actual:
        actual = "unknown"
    return actual in desired_values


def _matches_segment_filter(segment_filter: dict[str, Any] | None, *, side: str, regime: str, regime_v25: dict[str, Any], strategy_payload: dict[str, Any], bundle_mode_payload: bool) -> bool:
    if not segment_filter:
        return True
    trade_owner_key = (f"bundle:{strategy_payload.get('bundle_name') or strategy_payload.get('strategy_name')}" if bundle_mode_payload else f"single:{strategy_payload.get('version_id') or strategy_payload.get('strategy_name')}")
    values = {
        "side": side,
        "regime": regime,
        "regime_group": regime_v25.get("regime_group"),
        "regime_detail": regime_v25.get("regime_detail"),
        "strategy_mode": "bundle" if bundle_mode_payload else "single",
        "trade_owner_key": trade_owner_key,
    }
    for key, desired in dict(segment_filter or {}).items():
        if key not in values:
            continue
        if not _filter_match(values.get(key), desired):
            return False
    return True

def run_backtest(
    source_root: str | Path,
    symbols: list[str],
    strategy_payload: dict[str, Any],
    entry_timeframe: str = "5m",
    analysis_timeframe: str = "1h",
    start_date: str | None = None,
    end_date: str | None = None,
    config: dict[str, Any] | None = None,
) -> BacktestResult:
    config = dict(config or {})
    lookback_entry_bars = int(config.get("lookback_entry_bars", 300))
    max_hold_bars = int(config.get("max_hold_bars", 288))
    cooldown_bars = int(config.get("cooldown_bars", 3))
    fixed_stake_usd = float(config.get("fixed_stake_usd", 100))
    fee_bps_per_side = float(config.get("fee_bps_per_side", 0.0))
    slippage_bps_per_side = float(config.get("slippage_bps_per_side", 0.0))
    spread_bps = float(config.get("spread_bps", 0.0))
    funding_bps_per_8h = float(config.get("funding_bps_per_8h", 0.0))
    allow_long = bool(config.get("allow_long", True))
    allow_short = bool(config.get("allow_short", True))
    one_trade_at_time = bool(config.get("one_trade_at_time", True))
    entry_mode = str(config.get("entry_mode", "next_open") or "next_open").strip().lower()
    reverse_signal = bool(config.get("reverse_signal", False))
    segment_filter = dict(config.get("segment_filter") or {})

    bundle_mode_payload = is_bundle_payload(strategy_payload)
    slot = None if bundle_mode_payload else strategy_payload_to_slot(strategy_payload)
    symbols = [str(s).upper() for s in symbols]
    start_ts = pd.to_datetime(start_date, utc=True, errors="coerce") if start_date else None
    end_ts = pd.to_datetime(end_date, utc=True, errors="coerce") if end_date else None

    trade_rows: list[dict[str, Any]] = []
    higher_tfs = [tf for tf in HTF_MAP.get(analysis_timeframe, []) if timeframe_minutes(tf) > timeframe_minutes(analysis_timeframe)]

    def latest_row_before(frame: pd.DataFrame, values, ts):
        if frame.empty:
            return None
        idx = values.searchsorted(ts.to_datetime64(), side="right") - 1
        if idx < 0 or idx >= len(frame):
            return None
        row = frame.iloc[int(idx)]
        return row.to_dict()

    for symbol in symbols:
        base_df = load_bootstrap_frame(source_root, symbol, timeframe=entry_timeframe, start=start_ts, end=end_ts).reset_index(drop=True)
        if base_df.empty:
            continue

        entry_df = base_df if entry_timeframe == "5m" else resample_ohlcv(base_df, entry_timeframe)
        analysis_df = base_df if analysis_timeframe == entry_timeframe else resample_ohlcv(base_df, analysis_timeframe)
        higher_frames_full = {tf: resample_ohlcv(base_df, tf) for tf in higher_tfs}

        entry_features_df = enrich_features(entry_df).frame.sort_values("open_time").reset_index(drop=True)
        analysis_features_df = enrich_features(analysis_df).frame.sort_values("open_time").reset_index(drop=True)
        higher_features_full = {tf: enrich_features(df).frame.sort_values("open_time").reset_index(drop=True) for tf, df in higher_frames_full.items()}
        if entry_features_df.empty or analysis_features_df.empty:
            continue

        entry_times = entry_features_df["open_time"].to_numpy(dtype="datetime64[ns]")
        analysis_times = analysis_features_df["open_time"].to_numpy(dtype="datetime64[ns]")
        higher_times = {tf: df["open_time"].to_numpy(dtype="datetime64[ns]") for tf, df in higher_features_full.items()}

        min_bars = max(50, min(len(entry_features_df) - 2, max(30, lookback_entry_bars // 3)))
        i = min_bars
        next_allowed_index_by_side = {"LONG": i, "SHORT": i}
        while i < len(entry_features_df) - 2:
            entry_row_features = entry_features_df.iloc[i].to_dict()
            current_time = pd.to_datetime(entry_row_features["open_time"], utc=True, errors="coerce")
            analysis_row = latest_row_before(analysis_features_df, analysis_times, current_time)
            if not analysis_row:
                i += 1
                continue
            htf_context: dict[str, Any] = {analysis_timeframe: build_htf_row(analysis_row)}
            for tf, frame in higher_features_full.items():
                row = latest_row_before(frame, higher_times[tf], current_time)
                if row:
                    htf_context[tf] = build_htf_row(row)
            features = dict(entry_row_features)
            features.update(summarize_htf_context(htf_context))
            for tf, ctx in htf_context.items():
                for key, value in ctx.items():
                    features[f"htf_{tf}_{key}"] = value
            regime = classify_regime(features)
            regime_v25 = classify_detailed_regime(features)
            opinion = _bundle_opinion(features, strategy_payload) if bundle_mode_payload else score_from_slot(features, slot)
            if reverse_signal and opinion.bias in {"LONG", "SHORT"}:
                flipped_bias = "SHORT" if opinion.bias == "LONG" else "LONG"
                try:
                    from dataclasses import replace as _dc_replace
                    opinion = _dc_replace(opinion, bias=flipped_bias, note=f"Reverse signal scenario | {getattr(opinion, 'note', '')}".strip())
                except Exception:
                    try:
                        opinion = type(opinion)(
                            strategy_name=getattr(opinion, "strategy_name", "Strategy"),
                            strategy_id=getattr(opinion, "strategy_id", -1),
                            version_id=getattr(opinion, "version_id", -1),
                            version_no=getattr(opinion, "version_no", -1),
                            slot_id=getattr(opinion, "slot_id", -1),
                            template_key=getattr(opinion, "template_key", ""),
                            analyze=getattr(opinion, "analyze", True),
                            enabled=getattr(opinion, "enabled", True),
                            bias=flipped_bias,
                            score=getattr(opinion, "score", 0.0),
                            threshold=getattr(opinion, "threshold", 0.0),
                            note=f"Reverse signal scenario | {getattr(opinion, 'note', '')}".strip(),
                        )
                    except Exception:
                        pass
            if opinion.bias not in {"LONG", "SHORT"}:
                i += 1
                continue
            if one_trade_at_time and i < next_allowed_index_by_side.get(opinion.bias, i):
                i += 1
                continue
            if opinion.bias == "LONG" and not allow_long:
                i += 1
                continue
            if opinion.bias == "SHORT" and not allow_short:
                i += 1
                continue
            if not _matches_segment_filter(segment_filter, side=opinion.bias, regime=regime, regime_v25=regime_v25, strategy_payload=strategy_payload, bundle_mode_payload=bundle_mode_payload):
                i += 1
                continue

            entry_index = i + 1
            if entry_mode == "confirm_bar":
                confirm_index = i + 1
                if confirm_index >= len(entry_df) - 1:
                    break
                confirm_row = entry_df.iloc[confirm_index]
                signal_close = float(features.get("close") or entry_df.iloc[i].get("close") or 0.0)
                confirm_close = float(confirm_row.get("close") or confirm_row.get("open") or signal_close)
                is_confirmed = (opinion.bias == "LONG" and confirm_close > signal_close) or (opinion.bias == "SHORT" and confirm_close < signal_close)
                if not is_confirmed:
                    i += 1
                    continue
                entry_index = confirm_index + 1
                if entry_index >= len(entry_df):
                    break
            entry_row = entry_df.iloc[entry_index]
            entry_price = float(entry_row["open"])
            level_payload = add_exit_family_to_rule_params(strategy_payload)
            level_rule_params = dict(level_payload.get("rule_params") or {})
            trade_levels = build_trade_levels(features, opinion.bias, level_payload.get("expected_rr"), level_rule_params, entry_price=entry_price)
            if not trade_levels:
                i += 1
                continue
            future_df = entry_df.iloc[entry_index : entry_index + max_hold_bars].copy()
            if future_df.empty:
                break
            outcome = _simulate_trade_path(
                future_df,
                opinion.bias,
                trade_levels["entry_price"],
                trade_levels["stop_loss"],
                trade_levels["take_profit"],
                tp_levels=trade_levels.get("tp_levels"),
                late_trigger_index=trade_levels.get("late_trigger_index"),
                be_trigger_index=trade_levels.get("be_trigger_index"),
                lock_trigger_index=trade_levels.get("lock_trigger_index"),
                lock_to_tp_index=trade_levels.get("lock_to_tp_index"),
            )
            fee_cost_pct = (fee_bps_per_side * 2) / 100.0
            slippage_cost_pct = (slippage_bps_per_side * 2) / 100.0
            spread_cost_pct = spread_bps / 100.0
            exit_time = pd.to_datetime(outcome["close_time"], utc=True, errors="coerce")
            entry_time_for_cost = pd.to_datetime(entry_row["open_time"], utc=True, errors="coerce")
            held_hours_for_cost = 0.0
            if not pd.isna(exit_time) and not pd.isna(entry_time_for_cost):
                held_hours_for_cost = max(0.0, float((exit_time - entry_time_for_cost).total_seconds() / 3600.0))
            funding_cost_pct = (funding_bps_per_8h / 100.0) * (held_hours_for_cost / 8.0)
            execution_cost_pct = fee_cost_pct + slippage_cost_pct + spread_cost_pct + funding_cost_pct
            net_pnl_pct = float(outcome["pnl_pct"]) - execution_cost_pct
            trade_rows.append({
                "symbol": symbol,
                "entry_time": entry_row["open_time"],
                "signal_time": current_time,
                "exit_time": exit_time,
                "entry_timeframe": entry_timeframe,
                "analysis_timeframe": analysis_timeframe,
                "strategy_name": strategy_payload.get("bundle_name") or strategy_payload.get("strategy_name"),
                "version_no": int(strategy_payload.get("version_no") or 1),
                "strategy_mode": "bundle" if bundle_mode_payload else "single",
                "trade_owner_key": (f"bundle:{strategy_payload.get('bundle_name') or strategy_payload.get('strategy_name')}" if bundle_mode_payload else f"single:{strategy_payload.get('version_id') or strategy_payload.get('strategy_name')}"),
                "side": opinion.bias,
                "score": round(float(opinion.score), 2),
                "regime": regime,
                "regime_detail": regime_v25.get("regime_detail"),
                "regime_group": regime_v25.get("regime_group"),
                "regime_reason": regime_v25.get("regime_reason"),
                "trend_regime_score": regime_v25.get("trend_regime_score"),
                "range_regime_score": regime_v25.get("range_regime_score"),
                "squeeze_regime_score": regime_v25.get("squeeze_regime_score"),
                "panic_regime_score": regime_v25.get("panic_regime_score"),
                "signal_price": float(features.get("close") or trade_levels["entry_price"]),
                "entry_mode": entry_mode,
                "entry_price": float(trade_levels["entry_price"]),
                "stop_loss": float(trade_levels["stop_loss"]),
                "take_profit": float(trade_levels["take_profit"]),
                "risk_pct": float(trade_levels["risk_pct"]),
                "reward_pct": float(trade_levels["reward_pct"]),
                "expected_rr": trade_levels["expected_rr"],
                "tp_mode": trade_levels["tp_mode"],
                "tp_count": int(trade_levels["tp_count"]),
                "exit_family": trade_levels.get("exit_family"),
                "be_trigger_index": int(trade_levels.get("be_trigger_index") or 1),
                "lock_trigger_index": int(trade_levels.get("lock_trigger_index") or trade_levels.get("late_trigger_index") or 1),
                "lock_to_tp_index": int(trade_levels.get("lock_to_tp_index") or 1),
                "highest_tp_hit": int(outcome["highest_tp_hit"]),
                "close_reason": outcome["close_reason"],
                "outcome_label": outcome["outcome_label"],
                "close_price": float(outcome["close_price"]),
                "mfe_pct": float(outcome["mfe_pct"]),
                "mae_pct": float(outcome["mae_pct"]),
                "pnl_pct": round(net_pnl_pct, 4),
                "raw_pnl_pct": float(outcome["pnl_pct"]),
                "execution_cost_pct": round(float(execution_cost_pct), 4),
                "fee_cost_pct": round(float(fee_cost_pct), 4),
                "slippage_cost_pct": round(float(slippage_cost_pct), 4),
                "spread_cost_pct": round(float(spread_cost_pct), 4),
                "funding_cost_pct": round(float(funding_cost_pct), 4),
                "follow_through_score": float(outcome["follow_through_score"]),
                "bars_held": int(outcome["exit_offset"] + 1),
                "htf_alignment": features.get("htf_alignment"),
                "slippage_proxy_pct": round(abs(((float(trade_levels["entry_price"]) / float(features.get("close") or trade_levels["entry_price"])) - 1.0) * 100.0), 4),
                "feature_snapshot_json": json.dumps(features, ensure_ascii=False, default=str),
                "htf_context_json": json.dumps(htf_context, ensure_ascii=False, default=str),
                "bundle_components_json": json.dumps(strategy_payload.get("components") or [], ensure_ascii=False, default=str) if bundle_mode_payload else "",
            })
            if one_trade_at_time:
                next_allowed_index_by_side[opinion.bias] = entry_index + int(outcome["exit_offset"]) + cooldown_bars
            i += 1

    trades_df = pd.DataFrame(trade_rows)
    summary, equity_df, by_symbol_df, by_period_df, by_weekday_df, by_hour_df, by_day_df, by_week_df, by_month_df, by_side_df, by_regime_df, by_side_regime_df, by_exit_family_df, by_score_side_decile_df, by_score_decile_df, by_detailed_regime_df, by_side_detailed_regime_df, threshold_recommendations_df, by_owner_df, bundle_validation_df, friction_comparison_df, counterfactuals_df, conclusions, suggestions = build_backtest_summary(trades_df, fixed_stake_usd)
    run_config = {
        "source_root": str(source_root),
        "symbols": symbols,
        "entry_timeframe": entry_timeframe,
        "analysis_timeframe": analysis_timeframe,
        "start_date": str(start_ts) if start_ts is not None else None,
        "end_date": str(end_ts) if end_ts is not None else None,
        **config,
    }
    return BacktestResult(
        config=run_config,
        strategy_payload=strategy_payload,
        summary=summary,
        trades=trades_df,
        equity_curve=equity_df,
        performance_by_symbol=by_symbol_df,
        performance_by_period=by_period_df,
        performance_by_weekday=by_weekday_df,
        performance_by_hour=by_hour_df,
        performance_by_day=by_day_df,
        performance_by_week=by_week_df,
        performance_by_month=by_month_df,
        performance_by_side=by_side_df,
        performance_by_regime=by_regime_df,
        performance_by_side_regime=by_side_regime_df,
        performance_by_exit_family=by_exit_family_df,
        performance_by_score_side_decile=by_score_side_decile_df,
        performance_by_score_decile=by_score_decile_df,
        performance_by_detailed_regime=by_detailed_regime_df,
        performance_by_side_detailed_regime=by_side_detailed_regime_df,
        threshold_recommendations=threshold_recommendations_df,
        performance_by_owner=by_owner_df,
        bundle_validation=bundle_validation_df,
        friction_comparison=friction_comparison_df,
        counterfactuals=counterfactuals_df,
        conclusions=conclusions,
        suggestions=suggestions,
    )

def build_export_bundle_bytes(result: BacktestResult) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("summary.json", json.dumps(result.summary, indent=2, ensure_ascii=False))
        zf.writestr("config.json", json.dumps(result.config, indent=2, ensure_ascii=False))
        zf.writestr("strategy_payload.json", json.dumps(result.strategy_payload, indent=2, ensure_ascii=False))
        zf.writestr("conclusions.txt", "\n".join(result.conclusions + [""] + result.suggestions))
        zf.writestr("trades.csv", result.trades.to_csv(index=False))
        zf.writestr("equity_curve.csv", result.equity_curve.to_csv(index=False))
        zf.writestr("performance_by_symbol.csv", result.performance_by_symbol.to_csv(index=False))
        zf.writestr("performance_by_period.csv", result.performance_by_period.to_csv(index=False))
        zf.writestr("performance_by_weekday.csv", result.performance_by_weekday.to_csv(index=False))
        zf.writestr("performance_by_hour.csv", result.performance_by_hour.to_csv(index=False))
        zf.writestr("performance_by_day.csv", result.performance_by_day.to_csv(index=False))
        zf.writestr("performance_by_week.csv", result.performance_by_week.to_csv(index=False))
        zf.writestr("performance_by_month.csv", result.performance_by_month.to_csv(index=False))
        zf.writestr("performance_by_side.csv", result.performance_by_side.to_csv(index=False))
        zf.writestr("performance_by_regime.csv", result.performance_by_regime.to_csv(index=False))
        zf.writestr("performance_by_side_regime.csv", result.performance_by_side_regime.to_csv(index=False))
        zf.writestr("performance_by_exit_family.csv", result.performance_by_exit_family.to_csv(index=False))
        zf.writestr("performance_by_score_side_decile.csv", result.performance_by_score_side_decile.to_csv(index=False))
        zf.writestr("performance_by_score_decile.csv", result.performance_by_score_decile.to_csv(index=False))
        zf.writestr("performance_by_detailed_regime.csv", result.performance_by_detailed_regime.to_csv(index=False))
        zf.writestr("performance_by_side_detailed_regime.csv", result.performance_by_side_detailed_regime.to_csv(index=False))
        zf.writestr("threshold_recommendations.csv", result.threshold_recommendations.to_csv(index=False))
        zf.writestr("performance_by_owner.csv", result.performance_by_owner.to_csv(index=False))
        zf.writestr("bundle_validation.csv", result.bundle_validation.to_csv(index=False))
        zf.writestr("friction_comparison.csv", result.friction_comparison.to_csv(index=False))
        zf.writestr("counterfactuals.csv", result.counterfactuals.to_csv(index=False))
    return buffer.getvalue()


def save_backtest_result(result: BacktestResult, name: str, comment: str = "") -> Path:
    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    created_at = pd.Timestamp.now(tz=timezone.utc)
    run_dir = BACKTESTS_DIR / f"{created_at.strftime('%Y%m%d_%H%M%S')}_{_slugify(name)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "comment": comment,
        "created_at": created_at.isoformat(),
        "favorite": False,
        "run_kind": str((result.config or {}).get("run_kind") or "backtest"),
        "what_if_config": (result.config or {}).get("what_if_config") or {},
        "base_run": (result.config or {}).get("base_run") or {},
        "summary": result.summary,
        "config": result.config,
        "strategy_payload": result.strategy_payload,
        "conclusions": result.conclusions,
        "suggestions": result.suggestions,
        "files": {
            "trades": "trades.csv",
            "equity_curve": "equity_curve.csv",
            "performance_by_symbol": "performance_by_symbol.csv",
            "performance_by_period": "performance_by_period.csv",
            "performance_by_weekday": "performance_by_weekday.csv",
            "performance_by_hour": "performance_by_hour.csv",
            "performance_by_day": "performance_by_day.csv",
            "performance_by_week": "performance_by_week.csv",
            "performance_by_month": "performance_by_month.csv",
            "performance_by_side": "performance_by_side.csv",
            "performance_by_regime": "performance_by_regime.csv",
            "performance_by_side_regime": "performance_by_side_regime.csv",
            "performance_by_exit_family": "performance_by_exit_family.csv",
            "performance_by_score_side_decile": "performance_by_score_side_decile.csv",
            "performance_by_score_decile": "performance_by_score_decile.csv",
            "performance_by_detailed_regime": "performance_by_detailed_regime.csv",
            "performance_by_side_detailed_regime": "performance_by_side_detailed_regime.csv",
            "threshold_recommendations": "threshold_recommendations.csv",
            "performance_by_owner": "performance_by_owner.csv",
            "bundle_validation": "bundle_validation.csv",
            "friction_comparison": "friction_comparison.csv",
            "counterfactuals": "counterfactuals.csv",
            "bundle": "backtest_bundle.zip",
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    result.trades.to_csv(run_dir / "trades.csv", index=False)
    result.equity_curve.to_csv(run_dir / "equity_curve.csv", index=False)
    result.performance_by_symbol.to_csv(run_dir / "performance_by_symbol.csv", index=False)
    result.performance_by_period.to_csv(run_dir / "performance_by_period.csv", index=False)
    result.performance_by_weekday.to_csv(run_dir / "performance_by_weekday.csv", index=False)
    result.performance_by_hour.to_csv(run_dir / "performance_by_hour.csv", index=False)
    result.performance_by_day.to_csv(run_dir / "performance_by_day.csv", index=False)
    result.performance_by_week.to_csv(run_dir / "performance_by_week.csv", index=False)
    result.performance_by_month.to_csv(run_dir / "performance_by_month.csv", index=False)
    result.performance_by_side.to_csv(run_dir / "performance_by_side.csv", index=False)
    result.performance_by_regime.to_csv(run_dir / "performance_by_regime.csv", index=False)
    result.performance_by_side_regime.to_csv(run_dir / "performance_by_side_regime.csv", index=False)
    result.performance_by_exit_family.to_csv(run_dir / "performance_by_exit_family.csv", index=False)
    result.performance_by_score_side_decile.to_csv(run_dir / "performance_by_score_side_decile.csv", index=False)
    result.performance_by_score_decile.to_csv(run_dir / "performance_by_score_decile.csv", index=False)
    result.performance_by_detailed_regime.to_csv(run_dir / "performance_by_detailed_regime.csv", index=False)
    result.performance_by_side_detailed_regime.to_csv(run_dir / "performance_by_side_detailed_regime.csv", index=False)
    result.threshold_recommendations.to_csv(run_dir / "threshold_recommendations.csv", index=False)
    result.performance_by_owner.to_csv(run_dir / "performance_by_owner.csv", index=False)
    result.bundle_validation.to_csv(run_dir / "bundle_validation.csv", index=False)
    result.friction_comparison.to_csv(run_dir / "friction_comparison.csv", index=False)
    result.counterfactuals.to_csv(run_dir / "counterfactuals.csv", index=False)
    (run_dir / "summary.json").write_text(json.dumps(result.summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps(result.config, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "strategy_payload.json").write_text(json.dumps(result.strategy_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "backtest_bundle.zip").write_bytes(build_export_bundle_bytes(result))
    return run_dir


def list_saved_backtests() -> list[dict[str, Any]]:
    BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for manifest_path in sorted(BACKTESTS_DIR.glob("*/manifest.json"), reverse=True):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["run_dir"] = str(manifest_path.parent)
            items.append(manifest)
        except Exception:
            continue
    return items


def load_saved_backtest(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
    out = {"manifest": manifest, "run_dir": str(run_path)}
    for key, rel in (manifest.get("files") or {}).items():
        path = run_path / rel
        if path.suffix.lower() == ".csv" and path.exists():
            try:
                out[key] = pd.read_csv(path)
            except pd.errors.EmptyDataError:
                out[key] = pd.DataFrame()
        elif path.exists():
            out[key] = path
    return out


def update_saved_backtest_manifest(run_dir: str | Path, updates: dict[str, Any]) -> Path:
    run_path = Path(run_dir)
    manifest_path = run_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(updates or {})
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def delete_saved_backtest(run_dir: str | Path) -> None:
    run_path = Path(run_dir)
    if run_path.exists():
        shutil.rmtree(run_path, ignore_errors=True)
