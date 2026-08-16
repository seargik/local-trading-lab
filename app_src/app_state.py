from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .settings import (
    DEFAULT_ANALYSIS_TIMEFRAME,
    DEFAULT_CHART_TIMEFRAME,
    DEFAULT_POLL_SECONDS,
    DEFAULT_SELECTED_SYMBOLS,
    USER_CONFIG_PATH,
)


def _defaults() -> dict[str, Any]:
    return {
        "selected_symbols": DEFAULT_SELECTED_SYMBOLS,
        "timeframe": DEFAULT_ANALYSIS_TIMEFRAME,
        "analysis_timeframe": DEFAULT_ANALYSIS_TIMEFRAME,
        "chart_timeframe": DEFAULT_CHART_TIMEFRAME,
        "auto_paper_mode": False,
        "live_bundle_mode": False,
        "lookback": 300,
        "poll_seconds": DEFAULT_POLL_SECONDS,
    }


def load_user_config(path: Path = USER_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return _defaults()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        out = _defaults()
        if isinstance(data, dict):
            out.update(data)
            out["analysis_timeframe"] = data.get("analysis_timeframe") or data.get("timeframe") or DEFAULT_ANALYSIS_TIMEFRAME
            out["chart_timeframe"] = data.get("chart_timeframe") or DEFAULT_CHART_TIMEFRAME
            out["timeframe"] = out["analysis_timeframe"]
        return out
    except Exception:
        return _defaults()


def save_user_config(data: dict[str, Any], path: Path = USER_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = _defaults()
    analysis_timeframe = str(data.get("analysis_timeframe") or data.get("timeframe") or DEFAULT_ANALYSIS_TIMEFRAME)
    chart_timeframe = str(data.get("chart_timeframe") or DEFAULT_CHART_TIMEFRAME)
    safe.update(
        {
            "selected_symbols": data.get("selected_symbols", DEFAULT_SELECTED_SYMBOLS),
            "timeframe": analysis_timeframe,
            "analysis_timeframe": analysis_timeframe,
            "chart_timeframe": chart_timeframe,
            "auto_paper_mode": bool(data.get("auto_paper_mode", False)),
            "live_bundle_mode": bool(data.get("live_bundle_mode", False)),
            "lookback": int(data.get("lookback", 300) or 300),
            "poll_seconds": int(data.get("poll_seconds", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS),
        }
    )
    path.write_text(json.dumps(safe, indent=2), encoding="utf-8")
