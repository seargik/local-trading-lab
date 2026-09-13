from __future__ import annotations

import json

import pandas as pd

from app_src.backtest_core import strategy_payload_to_slot
from app_src.research_command_center_v29 import CORE_RESEARCH_FAMILIES, build_research_plan, select_core_strategies
from app_src.strategies import score_from_slot
from app_src.strategy_family_registry_v2811 import audit_strategies, family_readiness, load_registry_strategy_payload, resolve_entry


def _saved_strategy_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strategy_id": 1,
                "version_id": 11,
                "version_no": 1,
                "strategy_name": "HTF Bias + LTF Pullback Entry",
                "template_key": "rule_builder",
                "indicator_rules_json": "[]",
                "rule_params_json": "{}",
                "expected_rr": "1:3",
                "score_threshold": 70,
            },
            {
                "strategy_id": 2,
                "version_id": 22,
                "version_no": 1,
                "strategy_name": "Mean Reversion",
                "template_key": "range_reversion_trader",
                "indicator_rules_json": "[]",
                "rule_params_json": "{}",
                "expected_rr": "1:2",
                "score_threshold": 70,
            },
        ]
    )


def main() -> int:
    benchmark = load_registry_strategy_payload("ohlcv_compression_breakout_benchmark")
    reg = resolve_entry(benchmark["strategy_name"])
    assert reg["strategy_family"] == "compression_breakout"
    assert reg["historical_ready"] is True
    assert reg["benchmark_only"] is True
    assert reg["required_data"] == ["ohlcv"]

    long_features = {
        "breakout_above_n_bar_high": True,
        "breakout_below_n_bar_low": False,
        "compression_before_breakout": True,
        "volume_ratio": 1.5,
        "breakout_close_strength": 0.8,
        "htf_alignment": "bullish",
    }
    long_opinion = score_from_slot(long_features, strategy_payload_to_slot(benchmark))
    assert long_opinion.bias == "LONG"
    assert long_opinion.score >= 70

    short_features = {
        "breakout_above_n_bar_high": False,
        "breakout_below_n_bar_low": True,
        "compression_before_breakout": True,
        "volume_ratio": 1.5,
        "breakout_close_strength": 0.2,
        "htf_alignment": "bearish",
    }
    short_opinion = score_from_slot(short_features, strategy_payload_to_slot(benchmark))
    assert short_opinion.bias == "SHORT"
    assert short_opinion.score >= 70

    saved = _saved_strategy_rows()
    selected = select_core_strategies(saved, CORE_RESEARCH_FAMILIES, max_per_family=1)
    by_family = {row["research_family"]: row for row in selected}
    assert set(by_family) == set(CORE_RESEARCH_FAMILIES)
    assert by_family["compression_breakout"]["benchmark_only"] is True
    assert by_family["compression_breakout"]["research_source"] == "bundled_research_benchmark"

    plan = build_research_plan(selected, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert len(plan) == 9
    assert set(plan["research_family"]) == set(CORE_RESEARCH_FAMILIES)
    assert plan[plan["research_family"] == "compression_breakout"]["benchmark_only"].all()

    audit = audit_strategies(saved)
    readiness = family_readiness(audit)
    statuses = dict(zip(readiness["strategy_family"], readiness["status"]))
    assert all(statuses[family] == "ready" for family in CORE_RESEARCH_FAMILIES)

    json.dumps(benchmark)
    print("V28.12 OHLCV compression benchmark smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
