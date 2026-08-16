from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

from .exit_families import add_exit_family_to_rule_params
from .strategies import StrategyOpinion, score_from_slot

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
LIVE_BUNDLES_PATH = CONFIG_DIR / "live_bundle_presets.json"
EXAMPLE_BUNDLES_PATH = CONFIG_DIR / "bundle_strategy_examples.json"


def _strategy_payload_to_slot(payload: dict[str, Any], slot_id: int = 1) -> dict[str, Any]:
    payload = add_exit_family_to_rule_params(payload)
    return {
        "slot_id": slot_id,
        "strategy_id": int(payload.get("strategy_id") or -1),
        "version_id": int(payload.get("version_id") or -1),
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
        "exit_family": payload.get("exit_family"),
    }


def _normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _component_payload_from_slot(slot: dict[str, Any], component: dict[str, Any], idx: int) -> dict[str, Any]:
    payload = deepcopy(slot)
    payload["slot_id"] = int(slot.get("slot_id") or idx)
    payload.setdefault("strategy_name", component.get("strategy_name") or f"Component {idx}")
    params = dict(payload.get("rule_params") or {})
    if component.get("preferred_stop_multiplier") is not None:
        params["stop_multiplier"] = component.get("preferred_stop_multiplier")
    if component.get("preferred_entry_style"):
        params["preferred_entry_style"] = component.get("preferred_entry_style")
    payload["rule_params"] = params
    if component.get("min_score") is not None:
        payload["score_threshold"] = float(component.get("min_score"))
        payload["rule_params"]["score_threshold"] = float(component.get("min_score"))
    return add_exit_family_to_rule_params(payload)


def load_bundle_configs(path: str | Path | None = None) -> list[dict[str, Any]]:
    preferred = Path(path) if path else LIVE_BUNDLES_PATH
    fallback = EXAMPLE_BUNDLES_PATH
    for candidate in [preferred, fallback]:
        try:
            if candidate.exists():
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                bundles = raw.get("bundles") if isinstance(raw, dict) else raw
                if isinstance(bundles, list):
                    return [dict(x) for x in bundles if isinstance(x, dict)]
        except Exception:
            continue
    return []


def build_live_bundle_payloads(slot_rows: list[dict[str, Any]], symbol: str, enabled: bool = False, config_path: str | Path | None = None) -> list[dict[str, Any]]:
    if not enabled:
        return []
    by_name = {_normalize_name(row.get("strategy_name")): dict(row) for row in slot_rows if row.get("strategy_name") and bool(row.get("enabled", True)) and bool(row.get("analyze", True))}
    out: list[dict[str, Any]] = []
    symbol = str(symbol or "").upper()
    for bundle in load_bundle_configs(config_path):
        if bundle.get("enabled") is False:
            continue
        symbols = [str(s).upper() for s in (bundle.get("symbols") or [])]
        if symbols and symbol not in symbols:
            continue
        components = []
        missing = []
        for idx, component in enumerate(bundle.get("components") or [], start=1):
            name = _normalize_name(component.get("strategy_name"))
            slot = by_name.get(name)
            if not slot:
                missing.append(component.get("strategy_name"))
                continue
            components.append({
                "strategy_name": slot.get("strategy_name") or component.get("strategy_name"),
                "min_score": float(component.get("min_score") or slot.get("score_threshold") or 70),
                "weight": float((bundle.get("weights") or {}).get(slot.get("strategy_name"), component.get("weight", 1.0))),
                "preferred_entry_style": component.get("preferred_entry_style"),
                "preferred_stop_multiplier": component.get("preferred_stop_multiplier"),
                "strategy_payload": _component_payload_from_slot(slot, component, idx),
            })
        n_required = int(bundle.get("n_required") or max(1, len(components)))
        if len(components) < max(1, n_required):
            continue
        payload = {
            "strategy_type": "bundle",
            "strategy_name": bundle.get("bundle_name") or bundle.get("strategy_name") or "Live Bundle",
            "bundle_name": bundle.get("bundle_name") or bundle.get("strategy_name") or "Live Bundle",
            "bundle_mode": str(bundle.get("bundle_mode") or bundle.get("mode") or "n_of_m"),
            "n_required": n_required,
            "bundle_threshold": float(bundle.get("bundle_threshold") or n_required),
            "score_threshold": float(bundle.get("score_threshold") or 70),
            "expected_rr": str(bundle.get("expected_rr") or (components[0].get("strategy_payload") or {}).get("expected_rr") or "1:3"),
            "rule_params": dict(bundle.get("rule_params") or {}),
            "components": components,
            "missing_components": missing,
            "notes": bundle.get("notes") or "",
        }
        out.append(add_exit_family_to_rule_params(payload))
    return out


def score_bundle_opinion(features: dict[str, Any], payload: dict[str, Any]) -> StrategyOpinion:
    components = payload.get("components") or []
    if not components:
        return StrategyOpinion(payload.get("bundle_name") or "Bundle", -1, -1, 1, -100, "bundle", True, True, "WAIT", 0.0, float(payload.get("score_threshold") or 70), "No components configured")
    by_side: dict[str, list[tuple[dict[str, Any], StrategyOpinion]]] = {"LONG": [], "SHORT": []}
    notes = []
    for idx, component in enumerate(components, start=1):
        comp_payload = deepcopy(component.get("strategy_payload") or component)
        if component.get("min_score") is not None:
            comp_payload["score_threshold"] = float(component.get("min_score"))
            comp_payload.setdefault("rule_params", {})["score_threshold"] = float(component.get("min_score"))
        slot = _strategy_payload_to_slot(comp_payload, slot_id=int(comp_payload.get("slot_id") or idx))
        opinion = score_from_slot(features, slot)
        min_score = float(component.get("min_score") or opinion.threshold or 70)
        notes.append(f"{slot.get('strategy_name')}: {opinion.bias} {opinion.score:.1f}/{min_score:.1f}")
        if opinion.bias in {"LONG", "SHORT"} and float(opinion.score) >= min_score:
            by_side[opinion.bias].append((component, opinion))
    mode = str(payload.get("bundle_mode") or payload.get("mode") or "n_of_m").strip().lower()
    n_required = int(payload.get("n_required") or max(1, len(components)))
    bundle_threshold = float(payload.get("bundle_threshold") or n_required)
    weights = payload.get("weights") or {}
    side = "WAIT"
    score = 0.0
    if mode == "all_pass":
        for candidate in ["LONG", "SHORT"]:
            if len(by_side[candidate]) == len(components) and components:
                side = candidate
                score = min(op.score for _, op in by_side[candidate])
                break
    elif mode == "weighted_consensus":
        weighted = {}
        for candidate in ["LONG", "SHORT"]:
            weighted[candidate] = sum(float(weights.get(meta.get("strategy_name"), meta.get("weight", 1.0))) * float(op.score) / 100.0 for meta, op in by_side[candidate])
        best_side = max(weighted, key=weighted.get)
        other_side = "SHORT" if best_side == "LONG" else "LONG"
        if weighted[best_side] >= bundle_threshold and weighted[best_side] > weighted[other_side]:
            side = best_side
            denom = sum(float(weights.get(meta.get("strategy_name"), meta.get("weight", 1.0))) for meta, _ in by_side[best_side]) or 1.0
            score = round(weighted[best_side] * 100.0 / max(denom, 1.0), 2)
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
        slot_id=-100,
        template_key="bundle",
        analyze=True,
        enabled=True,
        bias=side,
        score=float(score),
        threshold=float(payload.get("score_threshold") or 70),
        note=" | ".join(notes),
    )


def bundle_opinion_dict(opinion: StrategyOpinion, payload: dict[str, Any]) -> dict[str, Any]:
    row = opinion.as_dict()
    row.update({
        "strategy_mode": "bundle",
        "trade_owner_key": f"bundle:{payload.get('bundle_name') or payload.get('strategy_name') or opinion.strategy_name}",
        "bundle_name": payload.get("bundle_name") or payload.get("strategy_name") or opinion.strategy_name,
        "bundle_mode": payload.get("bundle_mode") or payload.get("mode") or "n_of_m",
        "bundle_components": payload.get("components") or [],
        "expected_rr": payload.get("expected_rr") or "1:3",
        "rule_params": payload.get("rule_params") or {},
        "exit_family": (payload.get("rule_params") or {}).get("exit_family") or payload.get("exit_family"),
    })
    return row
