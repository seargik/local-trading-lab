from __future__ import annotations

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED_FILES = [
    "app_src/recommendation_actions.py",
    "app_src/backtest_core.py",
    "app_src/backtest_ui.py",
    "score_calibration_report.py",
    "config/recommendation_action_presets.json",
    "docs/HANDOVER_V26_PATCH.md",
    "docs/BABY_STEPS_V26.md",
]
REQUIRED_MARKERS = {
    "app_src/backtest_core.py": ["_matches_segment_filter", "segment_filter = dict(config.get"],
    "app_src/backtest_ui.py": ["V26 recommendation → what-if queue", "v26_recommendation_what_if"],
    "app_src/recommendation_actions.py": ["build_recommendation_what_if_tasks", "build_segment_filter_from_recommendation"],
    "score_calibration_report.py": ["saved_runs_v26_action_candidates.csv", "V26 Recommendation-to-Action Candidates"],
}


def main() -> None:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    if missing:
        raise SystemExit(f"Missing V26 files: {missing}")
    for rel in ["app_src/recommendation_actions.py", "app_src/backtest_core.py", "app_src/backtest_ui.py", "score_calibration_report.py", "backtest_worker.py"]:
        py_compile.compile(str(ROOT / rel), doraise=True)
    for rel, markers in REQUIRED_MARKERS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                raise SystemExit(f"Missing marker {marker!r} in {rel}")
    print("V26 smoke test passed: recommendation-to-action workflow files compile and markers exist.")


if __name__ == "__main__":
    main()
