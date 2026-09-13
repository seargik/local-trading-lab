from __future__ import annotations

import pandas as pd

from app_src.strategy_family_registry_v2811 import CORE_FAMILIES, audit_strategies, compiled_registry, family_readiness


def main() -> int:
    registry = compiled_registry()
    assert registry
    by_key = {row["registry_key"]: row for row in registry}
    assert by_key["htf_bias_ltf_pullback"]["strategy_family"] == "trend_pullback"
    assert by_key["mean_reversion"]["strategy_family"] == "range_reversion"
    assert by_key["compression_release_scalper"]["historical_ready"] is False
    assert by_key["ohlcv_compression_breakout_benchmark"]["historical_ready"] is True
    assert by_key["ohlcv_compression_breakout_benchmark"]["benchmark_only"] is True

    sample = pd.DataFrame([
        {"strategy_id": i + 1, "version_id": i + 1, "version_no": 1, "strategy_name": row["strategy_name"]}
        for i, row in enumerate(registry)
        if not row.get("benchmark_only")
    ])
    audit = audit_strategies(sample)
    readiness = family_readiness(audit)
    statuses = dict(zip(readiness["strategy_family"], readiness["status"]))
    assert statuses["trend_pullback"] == "ready"
    assert statuses["range_reversion"] == "ready"
    assert statuses["compression_breakout"] == "ready"
    assert set(statuses) == set(CORE_FAMILIES)
    print("V28.11 registry smoke test passed with V28.12 benchmark fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
