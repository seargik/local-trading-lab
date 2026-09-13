from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "config" / "strategy_family_registry.json"
BUNDLED_DIR = ROOT / "bundled_strategies"
CORE_FAMILIES = ["trend_pullback", "compression_breakout", "range_reversion"]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _load_registry_json() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def compiled_registry() -> list[dict[str, Any]]:
    data = _load_registry_json()
    available = set(data.get("available_historical_data") or ["ohlcv"])
    rows: list[dict[str, Any]] = []
    for key, cfg in data.get("entries", {}).items():
        path = BUNDLED_DIR / f"{key}.json"
        name = key
        file_exists = path.exists()
        if file_exists:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                name = str(raw.get("strategy", raw).get("strategy_name") or key)
            except Exception:
                pass
        required = list(cfg.get("requires") or ["ohlcv"])
        missing = [x for x in required if x not in available]
        benchmark_only = bool(cfg.get("benchmark_only", False))
        ready = bool(cfg.get("ready", False) and not missing and (file_exists or not benchmark_only))
        rows.append({
            "registry_key": key,
            "strategy_name": name,
            "strategy_family": str(cfg.get("family") or "unclassified"),
            "research_included": bool(cfg.get("research", False)),
            "historical_ready": ready,
            "required_data": required,
            "missing_historical_data": missing,
            "research_priority": int(cfg.get("priority") or 99),
            "registry_version": str(data.get("version") or ""),
            "benchmark_only": benchmark_only,
            "source": str(cfg.get("source") or "saved_strategy"),
            "bundled_file_exists": file_exists,
        })
    return rows


def resolve_entry(strategy_name: Any) -> dict[str, Any]:
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
        "benchmark_only": False,
        "source": "unclassified",
        "bundled_file_exists": False,
    }


def load_registry_strategy_payload(registry_key: str) -> dict[str, Any]:
    path = BUNDLED_DIR / f"{registry_key}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    strategy = dict(raw.get("strategy", raw))
    registry_row = next((x for x in compiled_registry() if x["registry_key"] == registry_key), None)
    if registry_row is None:
        raise KeyError(f"Strategy registry entry not found: {registry_key}")
    strategy.setdefault("strategy_id", 0)
    strategy.setdefault("version_id", 0)
    strategy.setdefault("version_no", 1)
    strategy["research_family"] = registry_row["strategy_family"]
    strategy["registry_key"] = registry_key
    strategy["registry_version"] = registry_row["registry_version"]
    strategy["research_source"] = registry_row["source"]
    strategy["benchmark_only"] = bool(registry_row["benchmark_only"])
    strategy["required_data"] = list(registry_row["required_data"])
    return strategy


def _audit_row(raw: dict[str, Any], reg: dict[str, Any], *, saved_in_library: bool) -> dict[str, Any]:
    ready = bool(reg["research_included"] and reg["historical_ready"] and reg["strategy_family"] in CORE_FAMILIES)
    if reg["strategy_family"] == "unclassified":
        status = "unclassified"
    elif reg["research_included"] and not reg["historical_ready"]:
        status = "blocked_missing_history"
    elif ready:
        status = "ready"
    else:
        status = "excluded"
    return {
        "strategy_id": raw.get("strategy_id"),
        "version_id": raw.get("version_id"),
        "version_no": raw.get("version_no"),
        "strategy_name": raw.get("strategy_name") or reg.get("strategy_name"),
        "strategy_family": reg["strategy_family"],
        "research_ready": ready,
        "required_data": ", ".join(reg["required_data"]),
        "missing_historical_data": ", ".join(reg["missing_historical_data"]),
        "research_priority": reg["research_priority"],
        "readiness": status,
        "benchmark_only": bool(reg.get("benchmark_only")),
        "source": reg.get("source"),
        "saved_in_library": bool(saved_in_library),
        "registry_key": reg.get("registry_key"),
    }


def audit_strategies(df: pd.DataFrame | None) -> pd.DataFrame:
    work = pd.DataFrame() if df is None else df.copy()
    if not work.empty and "version_no" in work.columns:
        work = work.sort_values(["strategy_id", "version_no"], ascending=[True, False]).drop_duplicates("strategy_id")

    rows: list[dict[str, Any]] = []
    seen_registry_keys: set[str] = set()
    if not work.empty:
        for _, raw_series in work.iterrows():
            raw = raw_series.to_dict()
            reg = resolve_entry(raw.get("strategy_name"))
            key = str(reg.get("registry_key") or "")
            if key:
                seen_registry_keys.add(key)
            rows.append(_audit_row(raw, reg, saved_in_library=True))

    for reg in compiled_registry():
        key = str(reg.get("registry_key") or "")
        if key in seen_registry_keys or not reg.get("benchmark_only"):
            continue
        rows.append(
            _audit_row(
                {
                    "strategy_id": None,
                    "version_id": None,
                    "version_no": 1,
                    "strategy_name": reg.get("strategy_name"),
                },
                reg,
                saved_in_library=False,
            )
        )
    return pd.DataFrame(rows)


def family_readiness(audit: pd.DataFrame | None) -> pd.DataFrame:
    audit = pd.DataFrame() if audit is None else audit
    rows: list[dict[str, Any]] = []
    for family in CORE_FAMILIES:
        fam = audit[audit["strategy_family"] == family] if not audit.empty else pd.DataFrame()
        ready = fam[fam["research_ready"] == True] if not fam.empty else pd.DataFrame()
        blocked = fam[fam["readiness"] == "blocked_missing_history"] if not fam.empty else pd.DataFrame()
        best = ready.sort_values(["research_priority", "benchmark_only"], ascending=[True, True]).iloc[0] if len(ready) else None
        rows.append({
            "strategy_family": family,
            "ready_strategies": int(len(ready)),
            "blocked_missing_history": int(len(blocked)),
            "status": "ready" if len(ready) else ("blocked_missing_history" if len(blocked) else "missing"),
            "best_ready_strategy": best["strategy_name"] if best is not None else None,
            "best_ready_source": best.get("source") if best is not None else None,
            "benchmark_only": bool(best.get("benchmark_only")) if best is not None else False,
        })
    return pd.DataFrame(rows)
