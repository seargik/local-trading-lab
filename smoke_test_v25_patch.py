from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = [
    "app_src/regime_v25.py",
    "app_src/calibration_v25.py",
    "app_src/overlap_analytics.py",
    "app_src/backtest_core.py",
    "app_src/backtest_ui.py",
    "score_calibration_report.py",
]
REQUIRED_MARKERS = {
    "app_src/backtest_core.py": [
        "classify_detailed_regime",
        "build_threshold_recommendations",
        "performance_by_detailed_regime.csv",
        "performance_by_side_detailed_regime.csv",
        "threshold_recommendations.csv",
        "regime_detail",
        "regime_group",
    ],
    "app_src/backtest_ui.py": [
        "V25 evidence details",
        "Detailed regime",
        "Thresholds",
        "Overlap",
        "build_overlap_reports",
    ],
    "score_calibration_report.py": [
        "saved_runs_detailed_regime.csv",
        "saved_runs_threshold_recommendations.csv",
        "saved_runs_overlap_same_side.csv",
    ],
}


def main() -> None:
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if not path.exists():
            raise SystemExit(f"Missing required V25 file: {rel}")
        py_compile.compile(str(path), doraise=True)
    for rel, markers in REQUIRED_MARKERS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        missing = [m for m in markers if m not in text]
        if missing:
            raise SystemExit(f"Missing V25 marker(s) in {rel}: {missing}")
    print("V25 smoke test passed: patched files compile and V25 markers exist.")


if __name__ == "__main__":
    main()
