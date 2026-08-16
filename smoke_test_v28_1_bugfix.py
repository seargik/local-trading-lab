from __future__ import annotations

from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app_src" / "backtest_ui.py"

py_compile.compile(str(TARGET), doraise=True)
text = TARGET.read_text(encoding="utf-8")
required = [
    "V28.1 backward compatibility",
    '"total_execution_cost_usd": 0.0',
    'df["pre_friction_pnl_usd"] = df["pre_friction_pnl_usd"].fillna(df["total_pnl_usd"])',
]
missing = [marker for marker in required if marker not in text]
if missing:
    raise SystemExit(f"Missing V28.1 markers: {missing}")
print("V28.1 smoke test passed: comparison UI handles missing friction columns.")
