from __future__ import annotations

from pathlib import Path

from app_src.trend_lifecycle import classify_trend_lifecycle

ROOT = Path(__file__).resolve().parent

required_files = [
    ".gitignore",
    ".devcontainer/devcontainer.json",
    ".github/workflows/smoke.yml",
    "README_CURRENT.md",
    "docs/STRATEGY_CAPABILITIES_REVIEW.md",
    "app_src/trend_lifecycle.py",
    "config/trend_lifecycle_rules.json",
    "tests/test_trend_lifecycle.py",
]

missing = [path for path in required_files if not (ROOT / path).exists()]
if missing:
    raise AssertionError(f"Missing repo-ready files: {missing}")

result = classify_trend_lifecycle(
    {
        "trend_regime_score": 65,
        "range_regime_score": 25,
        "squeeze_regime_score": 20,
        "panic_regime_score": 10,
        "adx_14": 25,
        "rsi_14": 59,
        "vwap_distance_pct": 0.004,
        "range_position_20": 0.52,
        "ma_stack_state": "bullish",
        "local_trend": "uptrend",
        "global_trend": "above_ma200",
    },
    symbol="ETHUSDT",
    analysis_tf="1h",
)

if result.lifecycle_state not in {"trend_pullback_entry", "trend_entering", "trend_running"}:
    raise AssertionError(f"Unexpected lifecycle state: {result.lifecycle_state}")
if result.trend_direction != "LONG":
    raise AssertionError(f"Unexpected direction: {result.trend_direction}")

app_py = (ROOT / "app.py").read_text(encoding="utf-8")
for marker in ["Market State", "classify_analysis_map", "with tab_lifecycle"]:
    if marker not in app_py:
        raise AssertionError(f"Missing app marker: {marker}")

print("V28.4 smoke test passed: repo-ready baseline and trend lifecycle scaffold are available.")
