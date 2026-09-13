from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app_src.prospective_paper_validation_v2824 import (
    evaluate_paper_session,
    initialize_paper_session,
    list_paper_session_freezes,
    load_paper_session_freeze,
    run_prospective_paper_cycle,
    verify_event_chain,
    verify_paper_session_freeze,
)


def _resolve_freeze(value: str) -> Path:
    direct = Path(value)
    if direct.exists():
        return direct
    for item in list_paper_session_freezes():
        record = item.get("record") or {}
        if str(record.get("session_id") or "") == value:
            return Path(item["path"])
    raise FileNotFoundError(f"No V28.24 paper freeze found for: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one V28.24 frozen prospective paper-validation cycle. This command never submits exchange orders."
    )
    parser.add_argument("--freeze", help="Path to the V28.24 freeze JSON, or its session_id")
    parser.add_argument("--list", action="store_true", help="List saved V28.24 paper sessions")
    parser.add_argument("--init", action="store_true", help="Initialize the session without requiring a new decision slot")
    parser.add_argument("--evaluate", action="store_true", help="Print the current evidence verdict after the cycle")
    args = parser.parse_args()

    if args.list:
        rows = []
        for item in list_paper_session_freezes():
            record = item.get("record") or {}
            rows.append({
                "session_id": record.get("session_id"),
                "created_at": record.get("created_at"),
                "first_eligible_decision_utc": record.get("first_eligible_decision_utc"),
                "target_end_utc": record.get("target_end_utc"),
                "path": item.get("path"),
                "integrity_ok": bool((item.get("verification") or {}).get("ok")),
            })
        print(json.dumps(rows, indent=2, default=str))
        return 0

    if not args.freeze:
        parser.error("--freeze is required unless --list is used")
    path = _resolve_freeze(args.freeze)
    record = load_paper_session_freeze(path)
    verification = verify_paper_session_freeze(record, check_current=True)
    if not verification.get("ok", False):
        print(json.dumps({"ok": False, "reason": "paper_freeze_integrity_failed", "verification": verification}, indent=2, default=str))
        return 2

    chain = verify_event_chain(record)
    if not chain.get("ok", False):
        print(json.dumps({"ok": False, "reason": "event_chain_integrity_failed", "event_chain": chain}, indent=2, default=str))
        return 3

    if args.init:
        state = initialize_paper_session(record)
        print(json.dumps({"ok": True, "session_id": record.get("session_id"), "status": state.get("status"), "live_execution_enabled": False}, indent=2, default=str))
        if not args.evaluate:
            return 0

    cycle = run_prospective_paper_cycle(record)
    output = {"cycle": cycle, "live_execution_enabled": False}
    if args.evaluate:
        result = evaluate_paper_session(record)
        output["verdict"] = result.verdict
        output["integrity"] = result.integrity
    print(json.dumps(output, indent=2, default=str))
    return 0 if cycle.get("ok", False) else 4


if __name__ == "__main__":
    sys.exit(main())
