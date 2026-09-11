from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app_src.runtime_cycle_v2810 import build_runtime_cycle_plan, load_runtime_cycle_config, run_runtime_cycle


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "runtime_cycle.json"
        cfg_path.write_text(json.dumps({
            "history": {"symbols": ["BTCUSDT", "ETHUSDT"], "intervals": ["1h", "4h"], "lookback": "12mo", "update_only": True},
            "analysis": {"mode": "inline", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h"},
            "research": {"queue": False, "symbols": ["BTCUSDT"], "families": ["trend_pullback"]}
        }), encoding="utf-8")
        cfg = load_runtime_cycle_config(cfg_path)
        plan = build_runtime_cycle_plan(cfg)
        assert len(plan["history_targets"]) == 4
        assert plan["history_lookback"] == "12mo"
        assert plan["analysis_mode"] == "inline"
        assert plan["queue_research"] is False
        dry = run_runtime_cycle(config_path=cfg_path, history_update=True, analysis_mode="inline", queue_research=False, dry_run=True)
        assert dry["status"] == "dry_run"
        assert dry["safe_mode"]["auto_paper_mode"] is False
        assert dry["safe_mode"]["live_execution"] is False
        assert len(dry["plan"]["history_targets"]) == 4

    required = [
        Path("app_src/runtime_cycle_v2810.py"),
        Path("config/runtime_cycle.json"),
        Path("docs/RUNTIME_CYCLE_V28_10.md"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    assert not missing, f"Missing V28.10 files: {missing}"
    print("V28.10 runtime cycle smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
