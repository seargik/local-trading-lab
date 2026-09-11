from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "config" / "strategy_family_registry.json"
BUNDLED_DIR = ROOT / "bundled_strategies"
CORE_FAMILIES = ["trend_pullback", "compression_breakout", "range_reversion"]


def _norm(value):
    return " ".join(str(value or "").strip().lower().split())


def compiled_registry():
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    available = set(data.get("available_historical_data") or ["ohlcv"])
    rows = []
    for key, cfg in data.get("entries", {}).items():
        path = BUNDLED_DIR / f"{key}.json"
        name = key
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                name = str(raw.get("strategy", raw).get("strategy_name") or key)
            except Exception:
                pass
        required = list(cfg.get("requires") or ["ohlcv"])
        missing = [x for x in required if x not in available]
        rows.append({
            "registry_key": key,
            "strategy_name": name,
            "strategy_family": str(cfg.get("family") or "unclassified"),
            "research_included": bool(cfg.get("research", False)),
            "historical_ready": bool(cfg.get("ready", False) and not missing),
            "required_data": required,
            "missing_historical_data": missing,
            "research_priority": int(cfg.get("priority") or 99),
            "registry_version": str(data.get("version") or ""),
        })
    return rows


def resolve_entry(strategy_name):
    lookup = {_norm(x["strategy_name"]): x for x in compiled_registry()}
    found = lookup.get(_norm(strategy_name))
    if found:
        return found
    return {
        "strategy_family": "unclassified",
        "research_included": False,
        "historical_ready": False,
        "required_data": [],
        "missing_historical_data": [],
        "research_priority": 999,
        "registry_key": "",
        "registry_version": "",
    }


def audit_strategies(df):
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "version_no" in work.columns:
        work = work.sort_values(["strategy_id", "version_no"], ascending=[True, False]).drop_duplicates("strategy_id")
    rows = []
    for _, raw in work.iterrows():
        reg = resolve_entry(raw.get("strategy_name"))
        ready = bool(reg["research_included"] and reg["historical_ready"] and reg["strategy_family"] in CORE_FAMILIES)
        if reg["strategy_family"] == "unclassified":
            status = "unclassified"
        elif reg["research_included"] and not reg["historical_ready"]:
            status = "blocked_missing_history"
        elif ready:
            status = "ready"
        else:
            status = "excluded"
        rows.append({
            "strategy_id": raw.get("strategy_id"),
            "version_id": raw.get("version_id"),
            "version_no": raw.get("version_no"),
            "strategy_name": raw.get("strategy_name"),
            "strategy_family": reg["strategy_family"],
            "research_ready": ready,
            "required_data": ", ".join(reg["required_data"]),
            "missing_historical_data": ", ".join(reg["missing_historical_data"]),
            "research_priority": reg["research_priority"],
            "readiness": status,
        })
    return pd.DataFrame(rows)


def family_readiness(audit):
    rows = []
    for family in CORE_FAMILIES:
        fam = audit[audit["strategy_family"] == family] if not audit.empty else pd.DataFrame()
        ready = fam[fam["research_ready"] == True] if not fam.empty else pd.DataFrame()
        blocked = fam[fam["readiness"] == "blocked_missing_history"] if not fam.empty else pd.DataFrame()
        rows.append({
            "strategy_family": family,
            "ready_strategies": int(len(ready)),
            "blocked_missing_history": int(len(blocked)),
            "status": "ready" if len(ready) else ("blocked_missing_history" if len(blocked) else "missing"),
            "best_ready_strategy": ready.sort_values("research_priority").iloc[0]["strategy_name"] if len(ready) else None,
        })
    return pd.DataFrame(rows)
