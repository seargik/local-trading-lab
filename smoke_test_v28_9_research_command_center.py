from __future__ import annotations

import compileall
from datetime import datetime, timezone

import pandas as pd

from app_src.research_command_center_v29 import (
    CORE_RESEARCH_FAMILIES,
    CORE_RESEARCH_SYMBOLS,
    DEFAULT_ANALYSIS_TIMEFRAME,
    DEFAULT_ENTRY_TIMEFRAME,
    build_research_plan,
    canonical_research_family,
    default_research_dates,
    select_core_strategies,
    strategy_row_to_payload,
)
from app_src.strategy_family_registry_v2811 import compiled_registry


def _row(strategy_id: int, name: str, version_no: int = 1) -> dict[str, object]:
    return {
        "strategy_id": strategy_id,
        "version_id": strategy_id * 10 + version_no,
        "version_no": version_no,
        "strategy_name": name,
        "template_key": "rule_builder",
        "human_thesis": "test",
        "expected_outcome": "test",
        "indicator_description": "",
        "indicators_json": "[]",
        "indicator_rules_json": "[]",
        "rule_params_json": "{}",
        "expected_rr": "1:3",
        "score_threshold": 70,
        "notes": "",
    }


def main() -> None:
    names = {row["registry_key"]: row["strategy_name"] for row in compiled_registry()}
    trend_name = names["htf_bias_ltf_pullback"]
    range_name = names["mean_reversion"]

    df = pd.DataFrame([
        _row(1, trend_name),
        _row(2, range_name),
    ])
    selected = select_core_strategies(df, CORE_RESEARCH_FAMILIES, max_per_family=1)
    assert len(selected) == 3, selected
    assert {x["research_family"] for x in selected} == set(CORE_RESEARCH_FAMILIES)
    compression = next(x for x in selected if x["research_family"] == "compression_breakout")
    assert compression["benchmark_only"] is True

    payload = strategy_row_to_payload(df.iloc[0])
    assert payload["strategy_name"] == trend_name
    assert canonical_research_family(payload) == "trend_pullback"

    plan = build_research_plan(selected, CORE_RESEARCH_SYMBOLS)
    assert len(plan) == 9
    assert set(plan["entry_timeframe"]) == {DEFAULT_ENTRY_TIMEFRAME}
    assert set(plan["analysis_timeframe"]) == {DEFAULT_ANALYSIS_TIMEFRAME}

    start, end = default_research_dates(365, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert start == "2025-09-11"
    assert end == "2026-09-11"

    assert compileall.compile_file("app_src/research_command_center_v29.py", quiet=1)
    assert compileall.compile_file("app_src/research_command_center_ui.py", quiet=1)
    assert compileall.compile_file("pages/00_Research_Command_Center.py", quiet=1)
    print("V28.9 smoke test passed: command center, registry-aware family selection, and research plan are available.")


if __name__ == "__main__":
    main()
