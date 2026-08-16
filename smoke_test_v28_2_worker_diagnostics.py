from pathlib import Path
import py_compile

required = [
    Path("app_src/worker_health.py"),
    Path("diagnose_backtest_worker.py"),
    Path("backtest_worker.py"),
    Path("app_src/backtest_ui.py"),
]
for path in required:
    if not path.exists():
        raise SystemExit(f"Missing required file: {path}")
    py_compile.compile(str(path), doraise=True)

ui = Path("app_src/backtest_ui.py").read_text(encoding="utf-8")
worker = Path("backtest_worker.py").read_text(encoding="utf-8")
if "Backtest worker heartbeat OK" not in ui:
    raise SystemExit("UI heartbeat marker missing")
if "write_worker_heartbeat" not in worker:
    raise SystemExit("Worker heartbeat marker missing")
print("V28.2 smoke test passed: worker heartbeat and diagnostics are available.")
