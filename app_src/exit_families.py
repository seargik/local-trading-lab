from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_EXIT_FAMILY = "breakout_balanced"

FAMILY_DEFAULTS: dict[str, dict[str, Any]] = {
    "trend_runner": {
        "exit_family": "trend_runner",
        "tp_mode": "structure_atr",
        "tp_count": 4,
        "tp_late_trigger_ratio": 0.78,
        "be_trigger_index": 2,
        "lock_trigger_index": 3,
        "lock_to_tp_index": 1,
        "structure_fractions": [0.32, 0.55, 0.78, 1.0],
        "notes": "Let trend/pullback/reclaim trades breathe; do not move to breakeven at TP1 by default.",
    },
    "breakout_balanced": {
        "exit_family": "breakout_balanced",
        "tp_mode": "structure_atr",
        "tp_count": 4,
        "tp_late_trigger_ratio": 0.70,
        "be_trigger_index": 1,
        "lock_trigger_index": 3,
        "lock_to_tp_index": 1,
        "structure_fractions": [0.25, 0.50, 0.75, 1.0],
        "notes": "Balanced breakout handling: protect after first expansion, but keep a final runner.",
    },
    "range_scalp": {
        "exit_family": "range_scalp",
        "tp_mode": "structure_atr",
        "tp_count": 3,
        "tp_late_trigger_ratio": 0.66,
        "be_trigger_index": 1,
        "lock_trigger_index": 2,
        "lock_to_tp_index": 1,
        "structure_fractions": [0.42, 0.72, 1.0],
        "notes": "Range/scalp exits harvest earlier because mean-reversion trades often decay after the midline.",
    },
    "reversal_defensive": {
        "exit_family": "reversal_defensive",
        "tp_mode": "fibo",
        "tp_count": 3,
        "tp_late_trigger_ratio": 0.66,
        "be_trigger_index": 1,
        "lock_trigger_index": 2,
        "lock_to_tp_index": 1,
        "structure_fractions": [0.38, 0.68, 1.0],
        "notes": "Defensive reversal handling: reduce giveback quickly after the first confirmation.",
    },
    "no_trade_gate": {
        "exit_family": "no_trade_gate",
        "tp_mode": "equal_rr",
        "tp_count": 2,
        "tp_late_trigger_ratio": 0.75,
        "be_trigger_index": 1,
        "lock_trigger_index": 2,
        "lock_to_tp_index": 1,
        "structure_fractions": [0.50, 1.0],
        "notes": "Gate/diagnostic strategy; should rarely own trades.",
    },
}

NAME_TO_FAMILY = {
    "htf pullback continuation": "trend_runner",
    "htf bias + ltf pullback entry": "trend_runner",
    "vwap reclaim trend continuation": "trend_runner",
    "trend following alignment rider": "trend_runner",
    "elliott wave proxy continuation": "trend_runner",
    "smc continuation reclaim": "trend_runner",
    "compression breakout + oi expansion": "breakout_balanced",
    "compression release scalper": "breakout_balanced",
    "rsi best practices regime trader": "breakout_balanced",
    "range rotation with midline rejection": "range_scalp",
    "market maker range scalper": "range_scalp",
    "mean reversion z-score reverter": "range_scalp",
    "failed breakout / liquidity sweep fade": "reversal_defensive",
    "smart money sweep reversal": "reversal_defensive",
    "oi/funding exhaustion reversal": "reversal_defensive",
    "order book absorption reversal": "reversal_defensive",
    "regime filter / no-trade gate": "no_trade_gate",
}


def _clean_family(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if text in FAMILY_DEFAULTS else None


def infer_exit_family(strategy_payload: dict[str, Any] | None = None, rule_params: dict[str, Any] | None = None) -> str:
    strategy_payload = strategy_payload or {}
    rule_params = rule_params or {}
    explicit = _clean_family(strategy_payload.get("exit_family") or rule_params.get("exit_family"))
    if explicit:
        return explicit
    name = str(strategy_payload.get("strategy_name") or rule_params.get("strategy_name") or "").strip().lower()
    if name in NAME_TO_FAMILY:
        return NAME_TO_FAMILY[name]
    for needle, family in NAME_TO_FAMILY.items():
        if needle and needle in name:
            return family
    template = str(strategy_payload.get("template_key") or rule_params.get("template_key") or "").strip().lower()
    if "range" in template or "reversion" in template:
        return "range_scalp"
    if "trend" in template or "pullback" in template:
        return "trend_runner"
    return DEFAULT_EXIT_FAMILY


def exit_family_profile(strategy_payload: dict[str, Any] | None = None, rule_params: dict[str, Any] | None = None) -> dict[str, Any]:
    strategy_payload = strategy_payload or {}
    rule_params = rule_params or {}
    family = infer_exit_family(strategy_payload, rule_params)
    profile = dict(FAMILY_DEFAULTS.get(family) or FAMILY_DEFAULTS[DEFAULT_EXIT_FAMILY])
    profile["exit_family"] = family
    # Explicit runtime knobs always win. This keeps strategy versions backwards-compatible.
    for key in [
        "tp_mode",
        "tp_count",
        "tp_late_trigger_ratio",
        "be_trigger_index",
        "lock_trigger_index",
        "lock_to_tp_index",
        "structure_fractions",
    ]:
        if key in rule_params and rule_params.get(key) not in {None, ""}:
            profile[key] = rule_params.get(key)
        elif key in strategy_payload and strategy_payload.get(key) not in {None, ""}:
            profile[key] = strategy_payload.get(key)
    return profile


def add_exit_family_to_rule_params(strategy_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(strategy_payload or {})
    params = dict(payload.get("rule_params") or {})
    params.setdefault("strategy_name", payload.get("strategy_name"))
    params.setdefault("template_key", payload.get("template_key"))
    params.setdefault("exit_family", infer_exit_family(payload, params))
    payload["exit_family"] = params["exit_family"]
    payload["rule_params"] = params
    return payload


def load_exit_family_templates(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else Path(__file__).resolve().parent.parent / "config" / "exit_family_templates.json"
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {"version": 1, "exit_families": FAMILY_DEFAULTS}
