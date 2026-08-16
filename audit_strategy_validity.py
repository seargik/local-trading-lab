from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
STRATEGY_DIR = ROOT / "bundled_strategies"
OUT_DIR = ROOT / "analysis_reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_packet(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("strategy") or raw


def audit_packet(packet: dict, source_file: str) -> dict:
    rules = packet.get("indicator_rules") or []
    threshold = float(packet.get("score_threshold") or (packet.get("rule_params") or {}).get("score_threshold") or 70)
    enabled = [r for r in rules if r and r.get("enabled", True)]
    long_weights = sum(float(r.get("weight") or 0) for r in enabled if str(r.get("bias") or "BOTH").upper() == "LONG")
    short_weights = sum(float(r.get("weight") or 0) for r in enabled if str(r.get("bias") or "BOTH").upper() == "SHORT")
    both_weights = sum(float(r.get("weight") or 0) for r in enabled if str(r.get("bias") or "BOTH").upper() == "BOTH")
    max_long = long_weights + both_weights / 2.0
    max_short = short_weights + both_weights / 2.0
    status = []
    if max_long < threshold:
        status.append("LONG_UNREACHABLE")
    if max_short < threshold:
        status.append("SHORT_UNREACHABLE")
    margin_long = round(max_long - threshold, 2)
    margin_short = round(max_short - threshold, 2)
    if 0 <= margin_long <= 5 or 0 <= margin_short <= 5:
        status.append("NEAR_PERFECT_CONFLUENCE")
    if not enabled:
        status.append("NO_ENABLED_RULES")
    if "NO_ENABLED_RULES" in status:
        action = "repair_rules_before_testing"
    elif "LONG_UNREACHABLE" in status and "SHORT_UNREACHABLE" in status:
        action = "lower_threshold_or_rebalance_weights"
    elif "LONG_UNREACHABLE" in status:
        action = "repair_long_side_or_mark_short_only_candidate"
    elif "SHORT_UNREACHABLE" in status:
        action = "repair_short_side_or_mark_long_only_candidate"
    elif "NEAR_PERFECT_CONFLUENCE" in status:
        action = "test_lower_threshold_or_relax_one_confirmation"
    else:
        action = "backtest_by_side_and_regime"
    return {
        "source_file": source_file,
        "strategy_name": packet.get("strategy_name"),
        "template_key": packet.get("template_key"),
        "score_threshold": threshold,
        "enabled_rules": len(enabled),
        "max_long_score": round(max_long, 2),
        "max_short_score": round(max_short, 2),
        "margin_long": margin_long,
        "margin_short": margin_short,
        "status": ", ".join(status) if status else "OK",
        "recommended_action": action,
    }


def main() -> None:
    rows = []
    for path in sorted(STRATEGY_DIR.glob("*.json")):
        try:
            packet = load_packet(path)
            rows.append(audit_packet(packet, path.name))
        except Exception as exc:
            rows.append({
                "source_file": path.name,
                "strategy_name": path.stem,
                "template_key": "",
                "score_threshold": "",
                "enabled_rules": "",
                "max_long_score": "",
                "max_short_score": "",
                "margin_long": "",
                "margin_short": "",
                "status": f"LOAD_ERROR: {exc}",
                "recommended_action": "fix_json_or_packet_shape",
            })
    df = pd.DataFrame(rows).sort_values(["status", "strategy_name"], ascending=[True, True])
    out_csv = OUT_DIR / "strategy_validity_audit.csv"
    out_md = OUT_DIR / "strategy_validity_audit.md"
    df.to_csv(out_csv, index=False)
    with out_md.open("w", encoding="utf-8") as f:
        f.write("# Strategy Validity Audit\n\n")
        f.write(df.to_markdown(index=False))
        f.write("\n")
    print(f"Saved: {out_csv}")
    print(f"Saved: {out_md}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
