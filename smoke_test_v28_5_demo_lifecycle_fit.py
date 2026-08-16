from __future__ import annotations

from pathlib import Path

from app_src.demo_mode import build_demo_candles, run_demo_scanner
from app_src.trend_lifecycle import evaluate_strategy_lifecycle_fit, classify_trend_lifecycle

ROOT = Path(__file__).resolve().parent

required_files = [
    "app_src/demo_mode.py",
    "demo_data/sample_ohlcv_fixture.json",
    "demo_data/README.md",
    "docs/HANDOVER_V28_5_DEMO_LIFECYCLE_FIT.md",
    "docs/BABY_STEPS_V28_5.md",
]
missing = [path for path in required_files if not (ROOT / path).exists()]
if missing:
    raise AssertionError(f"Missing V28.5 files: {missing}")

candles = build_demo_candles("ETHUSDT", "15m", 120)
if candles.empty or len(candles) != 120:
    raise AssertionError("Demo candles were not generated")
for col in ["open_time", "open", "high", "low", "close", "volume"]:
    if col not in candles.columns:
        raise AssertionError(f"Missing demo candle column: {col}")

scanner_rows, analysis_map = run_demo_scanner([], "15m")
if len(scanner_rows) < 3:
    raise AssertionError("Expected at least three demo scanner rows")
if not analysis_map:
    raise AssertionError("Expected demo analysis map")
for row in scanner_rows:
    for col in ["lifecycle_state", "lifecycle_direction", "lifecycle_confidence", "fit_ready_count"]:
        if col not in row:
            raise AssertionError(f"Missing scanner lifecycle column: {col}")

lifecycle = classify_trend_lifecycle(
    {
        "trend_regime_score": 66,
        "range_regime_score": 25,
        "squeeze_regime_score": 20,
        "panic_regime_score": 5,
        "adx_14": 26,
        "rsi_14": 58,
        "vwap_distance_pct": 0.005,
        "range_position_20": 0.52,
        "ma_stack_state": "bullish",
        "local_trend": "uptrend",
        "global_trend": "above_ma200",
    },
    symbol="ETHUSDT",
    analysis_tf="15m",
)
fit = evaluate_strategy_lifecycle_fit(
    {"strategy_name": "HTF Pullback Continuation", "template_key": "rule_builder", "bias": "LONG", "score": 75},
    lifecycle,
)
if fit.get("fit_status") != "fit" or not fit.get("allowed_by_lifecycle"):
    raise AssertionError(f"Expected lifecycle-fit HTF pullback, got {fit}")

app_py = (ROOT / "app.py").read_text(encoding="utf-8")
for marker in ["Demo mode / sample data", "run_demo_scanner", "fit_ready_count", "allowed_by_lifecycle"]:
    if marker not in app_py:
        raise AssertionError(f"Missing app marker: {marker}")

print("V28.5 smoke test passed: demo mode and lifecycle strategy-fit labels are available.")
