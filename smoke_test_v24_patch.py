"""V24 patch smoke test.

Run from project root:
    .\\.venv\\Scripts\\python.exe smoke_test_v24_patch.py

This intentionally avoids loading market data. It checks that the patched files compile
and that the V24 configs/exports are present.
"""
from __future__ import annotations

import json
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES_TO_COMPILE = [
    "app.py",
    "backtest_app.py",
    "backtest_worker.py",
    "score_calibration_report.py",
    "app_src/backtest_core.py",
    "app_src/backtest_ui.py",
    "app_src/engine.py",
    "app_src/bundle_engine.py",
    "app_src/exit_families.py",
]
REQUIRED_TEXT = {
    "app_src/backtest_core.py": [
        "execution_cost_pct",
        "friction_comparison",
        "bundle_validation",
        "performance_by_owner",
    ],
    "app_src/backtest_ui.py": [
        "Execution friction preset",
        "V24 comparison details",
        "Friction / bundle validation",
    ],
    "score_calibration_report.py": [
        "saved_runs_friction.csv",
        "saved_runs_owner_split.csv",
        "saved_runs_bundle_validation.csv",
    ],
}


def main() -> None:
    for rel in FILES_TO_COMPILE:
        py_compile.compile(str(ROOT / rel), doraise=True)
    preset_path = ROOT / "config" / "execution_friction_presets.json"
    presets = json.loads(preset_path.read_text(encoding="utf-8"))
    assert "zero_research" in presets, "Missing zero_research friction preset"
    assert "liquid_scalper_stress" in presets, "Missing scalper stress friction preset"
    for rel, needles in REQUIRED_TEXT.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"Missing {needle!r} in {rel}"
    print("V24 smoke test passed: patched files compile and V24 markers exist.")


if __name__ == "__main__":
    main()
