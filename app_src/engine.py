from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .features import build_htf_row, enrich_features, summarize_htf_context
from .exit_families import exit_family_profile, add_exit_family_to_rule_params
from .bundle_engine import bundle_opinion_dict, score_bundle_opinion
from .strategies import aggregate_final_view, classify_regime, score_from_slot

TP_MODE_OPTIONS = ["equal_rr", "fibo", "structure_atr"]
DEFAULT_TP_MODE = "structure_atr"
DEFAULT_LATE_TRIGGER_RATIO = 0.75
MAX_DISPLAY_TPS = 4


def parse_rr(rr_text: str | None, fallback: str = "1:3") -> tuple[float, float, str]:
    raw = (rr_text or fallback).strip().replace(" ", "")
    try:
        left, right = raw.split(":", 1)
        risk_pct = max(0.1, float(left))
        reward_pct = max(0.1, float(right))
        return risk_pct, reward_pct, f"{risk_pct:g}:{reward_pct:g}"
    except Exception:
        left, right = fallback.split(":", 1)
        return float(left), float(right), fallback


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return default


def resolve_tp_settings(expected_rr: str | None, rule_params: dict[str, Any] | None = None) -> dict[str, Any]:
    rule_params = rule_params or {}
    profile = exit_family_profile(rule_params=rule_params)
    risk_pct, reward_pct, rr_text = parse_rr(expected_rr)
    raw_mode = str(rule_params.get("tp_mode", profile.get("tp_mode") or DEFAULT_TP_MODE) or DEFAULT_TP_MODE).strip().lower()
    if raw_mode == "structure":
        raw_mode = "structure_atr"
    tp_mode = raw_mode if raw_mode in TP_MODE_OPTIONS else DEFAULT_TP_MODE
    explicit_count = _safe_float(rule_params.get("tp_count"))
    if explicit_count is not None and explicit_count > 0:
        tp_count = max(2, int(round(explicit_count)))
    else:
        family_count = _safe_float(profile.get("tp_count"))
        tp_count = max(2, int(round(family_count))) if family_count else max(2, min(4, int(round(reward_pct / max(risk_pct, 0.1)))))
    late_ratio = _safe_float(rule_params.get("tp_late_trigger_ratio"), _safe_float(profile.get("tp_late_trigger_ratio"), DEFAULT_LATE_TRIGGER_RATIO))
    if late_ratio is None:
        late_ratio = DEFAULT_LATE_TRIGGER_RATIO
    late_ratio = max(0.5, min(0.95, late_ratio))
    if tp_count <= 2:
        late_trigger_index = tp_count
    elif tp_count <= 4:
        late_trigger_index = min(tp_count, max(2, int(round(_safe_float(profile.get("lock_trigger_index"), 3) or 3))))
    else:
        late_trigger_index = max(3, int(math.ceil(tp_count * late_ratio)))
    be_trigger_index = max(1, min(tp_count, int(round(_safe_float(rule_params.get("be_trigger_index"), _safe_float(profile.get("be_trigger_index"), 1)) or 1))))
    lock_trigger_index = max(be_trigger_index, min(tp_count, int(round(_safe_float(rule_params.get("lock_trigger_index"), _safe_float(profile.get("lock_trigger_index"), late_trigger_index)) or late_trigger_index))))
    lock_to_tp_index = max(0, min(tp_count, int(round(_safe_float(rule_params.get("lock_to_tp_index"), _safe_float(profile.get("lock_to_tp_index"), 1)) or 1))))
    return {
        "risk_pct": float(risk_pct),
        "reward_pct": float(reward_pct),
        "expected_rr": rr_text,
        "tp_mode": tp_mode,
        "tp_count": int(tp_count),
        "tp_late_trigger_ratio": float(late_ratio),
        "late_trigger_index": int(late_trigger_index),
        "be_trigger_index": int(be_trigger_index),
        "lock_trigger_index": int(lock_trigger_index),
        "lock_to_tp_index": int(lock_to_tp_index),
        "exit_family": str(profile.get("exit_family") or "breakout_balanced"),
        "structure_fractions": profile.get("structure_fractions") or [],
    }



def normalize_slot_rows(slot_df) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if slot_df is None or len(slot_df) == 0:
        return rows

    def _clean_value(value: Any) -> Any:
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return value

    for _, row in slot_df.iterrows():
        payload = {k: _clean_value(v) for k, v in row.to_dict().items()}
        payload["indicators"] = _load_json(payload.get("indicators_json"), [])
        payload["indicator_rules"] = _load_json(payload.get("indicator_rules_json"), [])
        payload["rule_params"] = _load_json(payload.get("rule_params_json"), {})
        payload = add_exit_family_to_rule_params(payload)
        tp_cfg = resolve_tp_settings(payload.get("expected_rr"), payload.get("rule_params", {}))
        payload.update(tp_cfg)
        rows.append(payload)
    return rows



def analyze_symbol(df, slot_rows: list[dict[str, Any]], extras: dict[str, Any] | None = None, htf_frames: dict[str, Any] | None = None, bundle_payloads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    extras = extras or {}
    htf_frames = htf_frames or {}
    bundle_payloads = bundle_payloads or []
    feature_pack = enrich_features(df, extras=extras)
    if feature_pack.frame.empty:
        return {
            "features": {},
            "regime": "No data",
            "strategies": [],
            "bundles": [],
            "all_opinions": [],
            "summary": {
                "regime": "No data",
                "final_bias": "NONE",
                "final_score": 0.0,
                "recommendation": "No candles collected yet",
                "best_strategy": None,
            },
            "frame": feature_pack.frame,
            "htf_context": {},
        }

    features = feature_pack.latest.copy()
    htf_context: dict[str, Any] = {}
    for timeframe, frame in htf_frames.items():
        htf_pack = enrich_features(frame)
        if htf_pack.latest:
            htf_context[timeframe] = build_htf_row(htf_pack.latest)
    features.update(summarize_htf_context(htf_context))
    for timeframe, ctx in htf_context.items():
        for key, value in ctx.items():
            features[f"htf_{timeframe}_{key}"] = value
    regime = classify_regime(features)

    opinions = [score_from_slot(features, slot) for slot in slot_rows]
    bundle_pairs = [(payload, score_bundle_opinion(features, payload)) for payload in bundle_payloads]
    bundle_opinions = [op for _, op in bundle_pairs]
    summary = aggregate_final_view(regime, opinions + bundle_opinions)
    return {
        "features": features,
        "regime": regime,
        "strategies": [op.as_dict() for op in opinions],
        "bundles": [bundle_opinion_dict(op, payload) for payload, op in bundle_pairs],
        "all_opinions": [op.as_dict() for op in opinions] + [bundle_opinion_dict(op, payload) for payload, op in bundle_pairs],
        "summary": summary,
        "frame": feature_pack.frame,
        "htf_context": htf_context,
        "market_snapshot": extras,
    }


def _structure_fractions(features: dict[str, Any], bias: str, final_distance: float, atr_abs: float, exit_family: str | None = None, family_fractions: list[float] | None = None) -> list[float]:
    # Hybrid ATR / structure staging. Uses the final strategy target as the anchor,
    # but shapes interim exits based on volatility and where price sits in its recent range.
    range_pos = _safe_float(features.get("range_position_20"), 0.5)
    atr_share = 0.0 if final_distance <= 0 else min(1.0, max(0.0, atr_abs / final_distance))
    base = [float(x) for x in (family_fractions or []) if x is not None] or [0.28, 0.52, 0.78, 1.0]
    if atr_share >= 0.45:
        base = [0.22, 0.45, 0.72, 1.0]
    elif atr_share <= 0.2:
        base = [0.34, 0.60, 0.84, 1.0]
    if bias == "LONG":
        if range_pos is not None and range_pos >= 0.7:
            base = [max(0.18, base[0] - 0.04), max(0.40, base[1] - 0.03), max(0.68, base[2] - 0.02), 1.0]
        elif range_pos is not None and range_pos <= 0.3:
            base = [min(0.38, base[0] + 0.03), min(0.64, base[1] + 0.03), min(0.88, base[2] + 0.03), 1.0]
    else:
        if range_pos is not None and range_pos <= 0.3:
            base = [max(0.18, base[0] - 0.04), max(0.40, base[1] - 0.03), max(0.68, base[2] - 0.02), 1.0]
        elif range_pos is not None and range_pos >= 0.7:
            base = [min(0.38, base[0] + 0.03), min(0.64, base[1] + 0.03), min(0.88, base[2] + 0.03), 1.0]
    return sorted(max(0.05, min(1.0, x)) for x in base)


def _build_tp_levels(entry: float, final_tp: float, bias: str, tp_mode: str, tp_count: int, features: dict[str, Any], stop_loss: float, exit_family: str | None = None, family_fractions: list[float] | None = None) -> list[float]:
    if tp_count <= 0:
        tp_count = 1
    final_distance = abs(final_tp - entry)
    if final_distance <= 0:
        return [float(final_tp)]
    if tp_mode == "equal_rr":
        fractions = [i / tp_count for i in range(1, tp_count + 1)]
    elif tp_mode == "fibo":
        fib = [0.382, 0.5, 0.618, 1.0]
        if tp_count <= len(fib):
            fractions = fib[: tp_count - 1] + [1.0]
        else:
            inner = [(i / tp_count) for i in range(1, tp_count)]
            fractions = inner + [1.0]
    else:
        atr_abs = abs(_safe_float(features.get("atr_14"), 0.0) or 0.0)
        if atr_abs <= 0:
            atr_abs = abs(entry * (_safe_float(features.get("atr_pct"), 0.0) or 0.0))
        if atr_abs <= 0:
            atr_abs = abs(entry - stop_loss)
        fractions = _structure_fractions(features, bias, final_distance, atr_abs, exit_family=exit_family, family_fractions=family_fractions)
        if tp_count <= len(fractions):
            fractions = fractions[:tp_count - 1] + [1.0]
        else:
            inner = fractions[:-1]
            while len(inner) < tp_count - 1:
                remaining = tp_count - 1 - len(inner)
                step = (1.0 - inner[-1]) / (remaining + 1)
                inner.append(round(inner[-1] + step, 4))
            fractions = inner[:tp_count - 1] + [1.0]
    levels: list[float] = []
    for frac in fractions:
        if bias == "LONG":
            levels.append(float(entry + final_distance * frac))
        else:
            levels.append(float(entry - final_distance * frac))
    cleaned: list[float] = []
    last = None
    for value in levels:
        value = float(value)
        if last is not None and abs(value - last) < 1e-12:
            continue
        cleaned.append(value)
        last = value
    if not cleaned:
        cleaned = [float(final_tp)]
    cleaned[-1] = float(final_tp)
    return cleaned


def build_trade_levels(
    features: dict[str, Any],
    bias: str,
    expected_rr: str | None = None,
    rule_params: dict[str, Any] | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict[str, Any] | None:
    if bias not in {"LONG", "SHORT"}:
        return None
    cfg = resolve_tp_settings(expected_rr, rule_params)
    entry = float(entry_price if entry_price is not None else features["close"])
    stop_multiplier = _safe_float((rule_params or {}).get("stop_multiplier"), 1.0) or 1.0
    stop_multiplier = max(0.25, min(5.0, float(stop_multiplier)))
    risk_factor = (cfg["risk_pct"] / 100.0) * stop_multiplier
    reward_factor = cfg["reward_pct"] / 100.0

    if stop_loss is None:
        stop_loss = entry * (1 - risk_factor) if bias == "LONG" else entry * (1 + risk_factor)
    if take_profit is None:
        take_profit = entry * (1 + reward_factor) if bias == "LONG" else entry * (1 - reward_factor)

    stop_loss = float(stop_loss)
    take_profit = float(take_profit)
    tp_levels = _build_tp_levels(entry, take_profit, bias, cfg["tp_mode"], int(cfg["tp_count"]), features, stop_loss, exit_family=cfg.get("exit_family"), family_fractions=cfg.get("structure_fractions"))

    level_map = {f"tp{i+1}_price": (float(tp_levels[i]) if i < len(tp_levels) else None) for i in range(MAX_DISPLAY_TPS)}
    return {
        "entry_price": float(entry),
        "stop_loss": float(stop_loss),
        "stop_loss_initial": float(stop_loss),
        "stop_loss_current": float(stop_loss),
        "take_profit": float(take_profit),
        "risk_pct": float(cfg["risk_pct"]),
        "reward_pct": float(cfg["reward_pct"]),
        "expected_rr": cfg["expected_rr"],
        "tp_mode": cfg["tp_mode"],
        "tp_count": int(len(tp_levels)),
        "tp_late_trigger_ratio": float(cfg["tp_late_trigger_ratio"]),
        "late_trigger_index": int(min(cfg["late_trigger_index"], len(tp_levels))),
        "be_trigger_index": int(min(cfg.get("be_trigger_index", 1), len(tp_levels))),
        "lock_trigger_index": int(min(cfg.get("lock_trigger_index", cfg["late_trigger_index"]), len(tp_levels))),
        "lock_to_tp_index": int(min(cfg.get("lock_to_tp_index", 1), len(tp_levels))),
        "exit_family": cfg.get("exit_family"),
        "tp_levels": [float(x) for x in tp_levels],
        "highest_tp_hit": 0,
        "tp_hit_count": 0,
        "sl_state": "INITIAL",
        **level_map,
    }


def _tp_hit(side: str, row: pd.Series, target: float) -> bool:
    return bool(row["high"] >= target) if side == "LONG" else bool(row["low"] <= target)


def _sl_hit(side: str, row: pd.Series, stop: float) -> bool:
    return bool(row["low"] <= stop) if side == "LONG" else bool(row["high"] >= stop)


def _pnl_pct(side: str, entry: float, close: float) -> float:
    return ((close / entry) - 1.0) * (100 if side == "LONG" else -100)


def evaluate_trade_outcome(
    price_df,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    opened_at=None,
    tp_levels: list[float] | None = None,
    late_trigger_index: int | None = None,
    be_trigger_index: int | None = None,
    lock_trigger_index: int | None = None,
    lock_to_tp_index: int | None = None,
) -> dict[str, Any] | None:
    if price_df is None or price_df.empty:
        return None
    df = price_df.copy().sort_values("open_time").reset_index(drop=True)
    if opened_at is not None and "open_time" in df.columns:
        df = df[df["open_time"] >= opened_at].reset_index(drop=True)
    if df.empty:
        return None

    levels = [float(x) for x in (tp_levels or [take_profit]) if x is not None]
    if not levels:
        levels = [float(take_profit)]
    late_trigger_index = max(1, min(int(late_trigger_index or (3 if len(levels) >= 3 else len(levels))), len(levels)))
    be_trigger_index = max(1, min(int(be_trigger_index or 1), len(levels)))
    lock_trigger_index = max(be_trigger_index, min(int(lock_trigger_index or late_trigger_index), len(levels)))
    lock_to_tp_index = max(0, min(int(lock_to_tp_index or 1), len(levels)))

    if side == "LONG":
        mfe_pct = ((df["high"].max() / entry_price) - 1.0) * 100
        mae_pct = ((df["low"].min() / entry_price) - 1.0) * 100
    else:
        mfe_pct = ((entry_price / df["low"].min()) - 1.0) * 100
        mae_pct = ((entry_price / df["high"].max()) - 1.0) * 100 * -1

    current_sl = float(stop_loss)
    initial_sl = float(stop_loss)
    sl_state = "INITIAL"
    highest_tp_hit = 0
    tp_hits: dict[str, str] = {}
    last_price = float(df.iloc[-1]["close"])
    close_reason = None
    outcome_label = "OPEN"
    close_price = last_price
    status = "OPEN"

    for _, row in df.iterrows():
        candle_open = _safe_float(row.get("open"), _safe_float(row.get("close"), entry_price)) or entry_price
        next_tp = levels[highest_tp_hit] if highest_tp_hit < len(levels) else None
        sl_hit = _sl_hit(side, row, current_sl)
        tp_hit = _tp_hit(side, row, next_tp) if next_tp is not None else False

        if sl_hit and tp_hit and next_tp is not None:
            if abs(candle_open - current_sl) <= abs(candle_open - next_tp):
                tp_hit = False
            else:
                sl_hit = False

        if tp_hit and next_tp is not None:
            while highest_tp_hit < len(levels) and _tp_hit(side, row, levels[highest_tp_hit]):
                highest_tp_hit += 1
                tp_hits[str(highest_tp_hit)] = str(row.get("open_time"))
                if highest_tp_hit >= be_trigger_index and sl_state == "INITIAL":
                    current_sl = float(entry_price)
                    sl_state = "BREAKEVEN"
                if highest_tp_hit >= lock_trigger_index and lock_to_tp_index > 0:
                    lock_idx = min(lock_to_tp_index, len(levels)) - 1
                    current_sl = float(levels[lock_idx])
                    sl_state = f"AT_TP{lock_idx + 1}"
                if highest_tp_hit >= len(levels):
                    status = "CLOSED"
                    close_reason = "TAKE_PROFIT_FINAL"
                    outcome_label = f"TP{len(levels)}_FINAL"
                    close_price = float(levels[-1])
                    break
            if status == "CLOSED":
                break
            continue

        if sl_hit:
            status = "CLOSED"
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
            break

    follow_through_score = round(max(0.0, min(100.0, mfe_pct * 10 - abs(mae_pct) * 5 + highest_tp_hit * 6)), 2)
    outcome = {
        "status": status,
        "close_reason": close_reason,
        "close_price": float(close_price),
        "outcome_label": outcome_label,
        "mfe_pct": round(float(mfe_pct), 4),
        "mae_pct": round(float(mae_pct), 4),
        "pnl_pct": round(float(_pnl_pct(side, entry_price, close_price if status == "CLOSED" else last_price)), 4),
        "follow_through_score": follow_through_score,
        "stop_loss_current": float(current_sl),
        "stop_loss_initial": float(initial_sl),
        "sl_state": sl_state,
        "highest_tp_hit": int(highest_tp_hit),
        "tp_hit_count": int(highest_tp_hit),
        "tp_hits_json": tp_hits,
        "last_price": float(last_price),
    }
    for idx in range(MAX_DISPLAY_TPS):
        outcome[f"tp{idx+1}_hit_at"] = tp_hits.get(str(idx + 1))
    return outcome


def _load_json(raw: Any, fallback: Any) -> Any:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return __import__("json").loads(raw)
    except Exception:
        return fallback
