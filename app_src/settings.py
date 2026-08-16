from __future__ import annotations

from pathlib import Path

APP_NAME = "Local Lab v12 – HTF Context + LTF Entry"
EXCHANGE_NAME = "Binance Futures"
DEFAULT_CHART_TIMEFRAME = "5m"
DEFAULT_ANALYSIS_TIMEFRAME = "1h"
DEFAULT_TIMEFRAME = DEFAULT_ANALYSIS_TIMEFRAME
CHART_TIMEFRAME_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1d"]
ANALYSIS_TIMEFRAME_OPTIONS = ["5m", "15m", "1h", "4h"]
SUPPORTED_TIMEFRAMES = sorted(set(CHART_TIMEFRAME_OPTIONS + ANALYSIS_TIMEFRAME_OPTIONS + ["1w"]))
DEFAULT_LOOKBACK = 300
DEFAULT_POLL_SECONDS = 300
DEFAULT_SELECTED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT",
    "LINKUSDT", "DOTUSDT", "LTCUSDT", "UNIUSDT", "SUIUSDT", "APTUSDT", "NEARUSDT", "ATOMUSDT", "HBARUSDT",
    "AAVEUSDT", "ETCUSDT"
]
LAB_DB_PATH = Path("data/local_lab.sqlite")
MARKET_SNAPSHOT_PATH = Path("data/market_snapshot.json")
ANALYSIS_CACHE_PATH = Path("data/analysis_cache.json")
OHLCV_STORE_ROOT = Path("data/ohlcv_store")

BACKTEST_JOBS_ROOT = Path("data/backtest_jobs")
BACKTEST_JOBS_QUEUED_DIR = BACKTEST_JOBS_ROOT / "queued"
BACKTEST_JOBS_RUNNING_DIR = BACKTEST_JOBS_ROOT / "running"
BACKTEST_JOBS_COMPLETED_DIR = BACKTEST_JOBS_ROOT / "completed"
BACKTEST_JOBS_FAILED_DIR = BACKTEST_JOBS_ROOT / "failed"
DEFAULT_BOOTSTRAP_SOURCE_ROOT = Path("data_bootstrap/extracted")
BACKTEST_APP_PORT = 8503
COLLECTOR_PID_PATH = Path("data/collector.pid")
ANALYZER_PID_PATH = Path("data/analyzer.pid")
COLLECTOR_LOG_PATH = Path("logs/collector.log")
ANALYZER_LOG_PATH = Path("logs/analyzer.log")
COLLECTOR_STATE_PATH = Path("data/collector_state.json")
ANALYZER_STATE_PATH = Path("data/analyzer_state.json")
ANALYSIS_REQUEST_PATH = Path("data/analysis_request.json")
USER_CONFIG_PATH = Path("config/user_settings.json")
REFRESH_INTERVAL_MS = 60000
SYMBOL_LIST_LIMIT = 20
MAX_CHART_POINTS = 180
STRATEGY_SLOT_COUNT = 10
ANALYZER_POLL_SECONDS = 10
ANALYZE_ON_CLOSED_CANDLE_ONLY = True

BINANCE_FUTURES_REST = "https://fapi.binance.com"
HTF_MAP = {
    "1m": ["5m", "15m", "1h"],
    "5m": ["15m", "1h", "4h"],
    "15m": ["1h", "4h", "1d"],
    "1h": ["4h", "1d", "1w"],
    "4h": ["1d", "1w"],
    "1d": ["1w"],
}
