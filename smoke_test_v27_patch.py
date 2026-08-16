from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = [
    "app_src/promotion_v27.py",
    "app_src/backtest_ui.py",
    "score_calibration_report.py",
    "config/promotion_review_presets.json",
    "docs/HANDOVER_V27_PATCH.md",
    "docs/BABY_STEPS_V27.md",
]
REQUIRED_MARKERS = {
    "app_src/promotion_v27.py": ["build_promotion_candidates", "append_review_decision", "build_strategy_version_draft"],
    "app_src/backtest_ui.py": ["V27 Promotion & Review Lab", "Save V27 review decision", "Download V27 strategy draft JSON"],
    "score_calibration_report.py": ["saved_runs_v27_promotion_candidates.csv", "V27 Promotion Candidates"],
    "app_src/recommendation_actions.py": ["import json"],
}


def main() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    if missing:
        raise SystemExit(f"Missing V27 files: {missing}")
    for rel in ["app_src/promotion_v27.py", "app_src/recommendation_actions.py", "app_src/backtest_ui.py", "score_calibration_report.py"]:
        py_compile.compile(str(ROOT / rel), doraise=True)
    for rel, markers in REQUIRED_MARKERS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                raise SystemExit(f"Missing marker {marker!r} in {rel}")
    print("V27 smoke test passed: promotion/review workflow files compile and markers exist.")


if __name__ == "__main__":
    main()
