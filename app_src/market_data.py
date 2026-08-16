from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from .runtime_state import atomic_write_json, read_json
from .settings import (
    ANALYSIS_REQUEST_PATH,
    ANALYZER_LOG_PATH,
    ANALYZER_PID_PATH,
    ANALYZER_STATE_PATH,
    BINANCE_FUTURES_REST,
    COLLECTOR_LOG_PATH,
    COLLECTOR_PID_PATH,
    COLLECTOR_STATE_PATH,
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        if hasattr(value, "item"):
            value = value.item()
        return int(value)
    except BaseException:
        try:
            return int(float(str(value)))
        except BaseException:
            return default


class BinanceFuturesClient:
    exchange = "binance_futures"

    def __init__(self, timeout_seconds: int = 20) -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds

    def list_symbols(self) -> list[dict[str, Any]]:
        exchange_info = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/exchangeInfo", timeout=self.timeout_seconds).json()
        tickers = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/ticker/24hr", timeout=self.timeout_seconds).json()
        volume_map = {t["symbol"]: float(t.get("quoteVolume") or 0.0) for t in tickers if isinstance(t, dict)}
        symbols = []
        for item in exchange_info.get("symbols", []):
            if item.get("contractType") != "PERPETUAL" or item.get("quoteAsset") != "USDT" or item.get("status") != "TRADING":
                continue
            symbol = item["symbol"]
            symbols.append({"symbol": symbol, "baseAsset": item.get("baseAsset"), "quoteAsset": item.get("quoteAsset"), "quoteVolume": volume_map.get(symbol, 0.0)})
        symbols.sort(key=lambda x: x["quoteVolume"], reverse=True)
        return symbols

    def get_symbol_rule(self, symbol: str) -> dict[str, Any]:
        exchange_info = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/exchangeInfo", timeout=self.timeout_seconds).json()
        match = next((s for s in exchange_info.get("symbols", []) if s.get("symbol") == symbol.upper()), None)
        if not match:
            raise ValueError(f"Symbol not found: {symbol}")
        tick_size = step_size = min_notional = None
        for f in match.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick_size = float(f.get("tickSize") or 0.0)
            elif f.get("filterType") == "LOT_SIZE":
                step_size = float(f.get("stepSize") or 0.0)
            elif f.get("filterType") == "MIN_NOTIONAL":
                min_notional = float(f.get("notional") or 0.0)
        return {"exchange": self.exchange, "symbol": symbol.upper(), "tick_size": tick_size, "step_size": step_size, "min_notional": min_notional, "raw_json": match}

    def fetch_history(self, symbol: str, interval: str, limit: int = 300) -> list[dict[str, Any]]:
        payload = self.session.get(
            f"{BINANCE_FUTURES_REST}/fapi/v1/klines",
            params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
            timeout=self.timeout_seconds,
        ).json()
        return [self._normalize_kline(symbol.upper(), interval, row, source="rest") for row in payload]

    def fetch_open_interest_snapshot(self, symbol: str) -> dict[str, Any]:
        payload = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/openInterest", params={"symbol": symbol.upper()}, timeout=self.timeout_seconds).json()
        return {"open_interest": float(payload.get("openInterest") or 0.0)}

    def fetch_open_interest_history(self, symbol: str, period: str = "5m", limit: int = 40) -> list[float]:
        payload = self.session.get(f"{BINANCE_FUTURES_REST}/futures/data/openInterestHist", params={"symbol": symbol.upper(), "period": period, "limit": limit}, timeout=self.timeout_seconds).json()
        out: list[float] = []
        if isinstance(payload, list):
            for row in payload:
                try:
                    out.append(float(row.get("sumOpenInterest") or row.get("sumOpenInterestValue") or 0.0))
                except Exception:
                    continue
        return out

    def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        payload = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/premiumIndex", params={"symbol": symbol.upper()}, timeout=self.timeout_seconds).json()
        return {"funding_rate": float(payload.get("lastFundingRate") or 0.0), "mark_price": float(payload.get("markPrice") or 0.0)}

    def fetch_funding_rate_history(self, symbol: str, limit: int = 40) -> list[float]:
        payload = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/fundingRate", params={"symbol": symbol.upper(), "limit": limit}, timeout=self.timeout_seconds).json()
        out: list[float] = []
        if isinstance(payload, list):
            for row in payload:
                try:
                    out.append(float(row.get("fundingRate") or 0.0))
                except Exception:
                    continue
        return out

    def fetch_order_book_snapshot(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        payload = self.session.get(f"{BINANCE_FUTURES_REST}/fapi/v1/depth", params={"symbol": symbol.upper(), "limit": limit}, timeout=self.timeout_seconds).json()
        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        bid_qty = sum(float(x[1]) for x in bids)
        ask_qty = sum(float(x[1]) for x in asks)
        bid_qty_5 = sum(float(x[1]) for x in bids[:5])
        ask_qty_5 = sum(float(x[1]) for x in asks[:5])
        best_bid = float(bids[0][0]) if bids else None
        best_ask = float(asks[0][0]) if asks else None
        denom = bid_qty + ask_qty
        denom_5 = bid_qty_5 + ask_qty_5
        imbalance = ((bid_qty - ask_qty) / denom) if denom else 0.0
        imbalance_5 = ((bid_qty_5 - ask_qty_5) / denom_5) if denom_5 else 0.0
        spread_bps = ((best_ask - best_bid) / ((best_ask + best_bid) / 2) * 10000) if best_bid and best_ask else None
        max_ask = max((float(x[1]) for x in asks[:10]), default=0.0)
        max_bid = max((float(x[1]) for x in bids[:10]), default=0.0)
        depth_wall_above_pct = (max_ask / ask_qty) if ask_qty else 0.0
        depth_wall_below_pct = (max_bid / bid_qty) if bid_qty else 0.0
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_qty_20": bid_qty,
            "ask_qty_20": ask_qty,
            "bid_qty_5": bid_qty_5,
            "ask_qty_5": ask_qty_5,
            "order_book_imbalance": imbalance,
            "order_book_imbalance_5_levels": imbalance_5,
            "order_book_spread_bps": spread_bps,
            "depth_wall_above_pct": depth_wall_above_pct,
            "depth_wall_below_pct": depth_wall_below_pct,
        }

    def fetch_market_snapshot(self, symbol: str, oi_period: str = "5m") -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        for fn in [self.fetch_funding_rate, self.fetch_open_interest_snapshot, self.fetch_order_book_snapshot]:
            try:
                snapshot.update(fn(symbol))
            except Exception:
                continue
        try:
            snapshot["open_interest_history"] = self.fetch_open_interest_history(symbol, period=oi_period, limit=40)
        except Exception:
            snapshot["open_interest_history"] = []
        try:
            snapshot["funding_rate_history"] = self.fetch_funding_rate_history(symbol, limit=40)
        except Exception:
            snapshot["funding_rate_history"] = []
        return snapshot

    def _normalize_kline(self, symbol: str, interval: str, row: list[Any], source: str) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": symbol,
            "interval": interval,
            "open_time": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": datetime.fromtimestamp(int(row[6]) / 1000, tz=timezone.utc).isoformat(),
            "is_closed": bool(row[11]) if len(row) > 11 else True,
            "source": source,
        }


@dataclass
class CollectorStatus:
    running: bool = False
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1h"
    analysis_timeframe: str = "1h"
    chart_timeframe: str = "5m"
    last_error: str | None = None
    last_event_time: str | None = None
    reconnects: int = 0
    history_bootstrapped: bool = False
    heartbeat_at: str | None = None
    poll_seconds: int = 300


@dataclass
class AnalyzerStatus:
    running: bool = False
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1h"
    analysis_timeframe: str = "1h"
    chart_timeframe: str = "5m"
    last_error: str | None = None
    last_analyzed_candle_time: str | None = None
    last_run_at: str | None = None
    analysis_stale: bool = False
    heartbeat_at: str | None = None


class _WorkerServiceBase:
    pid_setting = None
    log_setting = None
    state_setting = None
    worker_filename = None

    def __init__(self, storage=None) -> None:
        self.storage = storage

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def _pid_path(self) -> Path:
        return (self._project_root() / self.pid_setting).resolve()

    def _log_path(self) -> Path:
        return (self._project_root() / self.log_setting).resolve()

    def _state_path(self) -> Path:
        return (self._project_root() / self.state_setting).resolve()

    def _worker_script(self) -> Path:
        return (self._project_root() / self.worker_filename).resolve()

    def _read_pid(self) -> int | None:
        try:
            raw = self._pid_path().read_text(encoding='utf-8').strip()
            return int(raw) if raw else None
        except Exception:
            return None

    def _write_pid(self, pid: int) -> None:
        path = self._pid_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid), encoding='utf-8')

    def _clear_pid(self, pid: int | None = None) -> None:
        path = self._pid_path()
        if not path.exists():
            return
        if pid is None:
            try:
                path.unlink()
            except Exception:
                pass
            return
        try:
            existing = int(path.read_text(encoding='utf-8').strip())
            if existing == int(pid):
                path.unlink()
        except Exception:
            pass

    def _pid_running(self, pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            if sys.platform.startswith('win'):
                completed = subprocess.run(['tasklist', '/FI', f'PID eq {int(pid)}'], capture_output=True, text=True, check=False)
                output = (completed.stdout or '') + (completed.stderr or '')
                return str(int(pid)) in output
            import os
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _spawn(self, args: list[str] | None = None) -> int:
        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_path, 'a', encoding='utf-8')
        py = self._project_root() / '.venv' / 'Scripts' / 'python.exe'
        python_exe = str(py if py.exists() else Path(sys.executable))
        cmd = [python_exe, str(self._worker_script()), *(args or [])]
        kwargs: dict[str, Any] = {'cwd': str(self._project_root()), 'stdout': log_handle, 'stderr': log_handle, 'stdin': subprocess.DEVNULL, 'close_fds': True}
        if sys.platform.startswith('win'):
            kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['start_new_session'] = True
        proc = subprocess.Popen(cmd, **kwargs)
        self._write_pid(proc.pid)
        return proc.pid

    def _stop_pid(self, pid: int | None) -> None:
        if pid:
            try:
                if sys.platform.startswith('win'):
                    subprocess.run(['taskkill', '/PID', str(pid), '/F'], check=False, capture_output=True)
                else:
                    import os, signal
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        self._clear_pid(pid)


class CollectorService(_WorkerServiceBase):
    pid_setting = COLLECTOR_PID_PATH
    log_setting = COLLECTOR_LOG_PATH
    state_setting = COLLECTOR_STATE_PATH
    worker_filename = 'collector_worker.py'

    def __init__(self, storage=None) -> None:
        super().__init__(storage)
        self.client = BinanceFuturesClient()

    def start(self, symbols: list[str], analysis_timeframe: str, chart_timeframe: str, lookback: int, poll_seconds: int = 300) -> None:
        symbols = [s.upper().strip() for s in symbols if s and s.strip()]
        if not symbols:
            raise ValueError('At least one symbol must be selected')
        if self.get_status().running:
            return
        self._spawn([
            '--symbols', ','.join(symbols),
            '--analysis-timeframe', analysis_timeframe,
            '--chart-timeframe', chart_timeframe,
            '--lookback', str(int(lookback)),
            '--poll-seconds', str(int(poll_seconds)),
        ])

    def stop(self) -> None:
        pid = self._read_pid() or read_json(self._state_path(), {}).get('pid')
        self._stop_pid(pid)

    def get_status(self) -> CollectorStatus:
        state = read_json(self._state_path(), {'running': False, 'symbols': [], 'timeframe': '1h', 'analysis_timeframe': '1h', 'chart_timeframe': '5m', 'reconnects': 0, 'history_bootstrapped': False, 'poll_seconds': 300})
        pid = state.get('pid') or self._read_pid()
        pid_alive = self._pid_running(pid)
        running = bool(state.get('running')) and pid_alive
        if not running and pid and not pid_alive:
            self._clear_pid(pid)
        return CollectorStatus(
            running=running,
            symbols=list(state.get('symbols') or []),
            timeframe=state.get('analysis_timeframe') or state.get('timeframe') or '1h',
            analysis_timeframe=state.get('analysis_timeframe') or state.get('timeframe') or '1h',
            chart_timeframe=state.get('chart_timeframe') or '5m',
            last_error=state.get('last_error'),
            last_event_time=state.get('last_event_time'),
            reconnects=_safe_int(state.get('reconnects'), 0),
            history_bootstrapped=bool(state.get('history_bootstrapped', False)),
            heartbeat_at=state.get('heartbeat_at'),
            poll_seconds=_safe_int(state.get('poll_seconds'), 300),
        )

    def list_symbols(self) -> list[dict[str, Any]]:
        return self.client.list_symbols()


class AnalyzerService(_WorkerServiceBase):
    pid_setting = ANALYZER_PID_PATH
    log_setting = ANALYZER_LOG_PATH
    state_setting = ANALYZER_STATE_PATH
    worker_filename = 'analyzer_worker.py'

    def start(self) -> None:
        if self.get_status().running:
            return
        self._spawn([])

    def stop(self) -> None:
        pid = self._read_pid() or read_json(self._state_path(), {}).get('pid')
        self._stop_pid(pid)

    def request_run(self, reason: str = 'manual') -> None:
        atomic_write_json((self._project_root() / ANALYSIS_REQUEST_PATH).resolve(), {'requested_at': datetime.now(timezone.utc).isoformat(), 'reason': reason})

    def get_status(self) -> AnalyzerStatus:
        state = read_json(self._state_path(), {'running': False, 'symbols': [], 'timeframe': '1h', 'analysis_timeframe': '1h', 'chart_timeframe': '5m'})
        pid = state.get('pid') or self._read_pid()
        pid_alive = self._pid_running(pid)
        running = bool(state.get('running')) and pid_alive
        if not running and pid and not pid_alive:
            self._clear_pid(pid)
        return AnalyzerStatus(
            running=running,
            symbols=list(state.get('symbols') or []),
            timeframe=state.get('analysis_timeframe') or state.get('timeframe') or '1h',
            analysis_timeframe=state.get('analysis_timeframe') or state.get('timeframe') or '1h',
            chart_timeframe=state.get('chart_timeframe') or '5m',
            last_error=state.get('last_error'),
            last_analyzed_candle_time=state.get('last_analyzed_candle_time'),
            last_run_at=state.get('last_run_at'),
            analysis_stale=bool(state.get('analysis_stale', False)),
            heartbeat_at=state.get('heartbeat_at'),
        )
