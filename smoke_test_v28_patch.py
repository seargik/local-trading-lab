from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = [
    ROOT / "app_src" / "cross_validation_v28.py",
    ROOT / "app_src" / "backtest_ui.py",
    ROOT / "backtest_worker.py",
    ROOT / "score_calibration_report.py",
]
for path in FILES:
    if not path.exists():
        raise SystemExit(f"Missing expected V28 file: {path}")
    py_compile.compile(str(path), doraise=True)

checks = {
    ROOT / "app_src" / "cross_validation_v28.py": ["queue_cross_validation_jobs", "build_cv_reports", "save_draft_as_strategy_version"],
    ROOT / "app_src" / "backtest_ui.py": ["V28 Strategy Draft Import + Cross-Validation Lab", "Queue V28 cross-validation jobs"],
    ROOT / "backtest_worker.py": ["task_meta", "config_overrides"],
    ROOT / "score_calibration_report.py": ["saved_runs_v28_cv_aggregate.csv", "V28 Cross-Validation Aggregate"],
}
for path, markers in checks.items():
    text = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            raise SystemExit(f"Missing marker {marker!r} in {path}")

print("V28 smoke test passed: cross-validation workflow files compile and markers exist.")
