from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .runtime_state import atomic_write_json, read_json
from .settings import ANALYSIS_CACHE_PATH, LAB_DB_PATH, MARKET_SNAPSHOT_PATH, STRATEGY_SLOT_COUNT
from .ohlcv_store import load_recent_candles


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(raw: Any, fallback: Any) -> Any:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _prepare_analysis_map_for_cache(raw_map: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for symbol, analysis in (raw_map or {}).items():
        if not isinstance(analysis, dict):
            continue
        item = dict(analysis)
        frame = item.get("frame")
        if isinstance(frame, pd.DataFrame):
            item["frame"] = frame.to_dict(orient="records")
        out[str(symbol)] = item
    return out


def _restore_analysis_map_from_cache(raw_map: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(raw_map, dict):
        return out
    for symbol, analysis in raw_map.items():
        if not isinstance(analysis, dict):
            continue
        item = dict(analysis)
        frame = item.get("frame")
        if isinstance(frame, list):
            item["frame"] = pd.DataFrame(frame)
        elif isinstance(frame, dict):
            item["frame"] = pd.DataFrame(frame)
        elif not isinstance(frame, pd.DataFrame):
            item["frame"] = pd.DataFrame()
        out[str(symbol)] = item
    return out


def _coerce_bool(v: Any) -> int:
    return 1 if bool(v) else 0


class Storage:
    def __init__(self, lab_db_path: Path | str = LAB_DB_PATH, market_db_path: Path | str | None = None, analysis_db_path: Path | str | None = None) -> None:
        self.lab_db_path = Path(lab_db_path)
        self.market_snapshot_path = Path(MARKET_SNAPSHOT_PATH)
        self.analysis_cache_path = Path(ANALYSIS_CACHE_PATH)
        self._lock = threading.RLock()
        self._init_db()
        self._seed_defaults_if_needed()

    def _connect(self) -> sqlite3.Connection:
        self.lab_db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.lab_db_path, timeout=30, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA foreign_keys=ON;")
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS strategies (
                        strategy_id INTEGER PRIMARY KEY,
                        strategy_name TEXT NOT NULL,
                        template_key TEXT NOT NULL,
                        retired INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS strategy_versions (
                        version_id INTEGER PRIMARY KEY,
                        strategy_id INTEGER NOT NULL,
                        version_no INTEGER NOT NULL,
                        is_active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        human_thesis TEXT,
                        expected_outcome TEXT,
                        indicator_description TEXT,
                        indicators_json TEXT,
                        indicator_rules_json TEXT,
                        rule_params_json TEXT,
                        expected_rr TEXT,
                        score_threshold REAL,
                        notes TEXT,
                        UNIQUE(strategy_id, version_no),
                        FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                    );

                    CREATE TABLE IF NOT EXISTS strategy_slots (
                        slot_id INTEGER PRIMARY KEY,
                        version_id INTEGER,
                        analyze_flag INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT,
                        FOREIGN KEY(version_id) REFERENCES strategy_versions(version_id)
                    );

                    CREATE TABLE IF NOT EXISTS signal_events (
                        signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        signal_key TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        symbol TEXT,
                        interval TEXT,
                        bar_open_time TEXT,
                        slot_id INTEGER,
                        strategy_id INTEGER,
                        version_id INTEGER,
                        strategy_name TEXT,
                        version_no INTEGER,
                        regime TEXT,
                        bias TEXT,
                        score REAL,
                        recommendation TEXT,
                        setup_summary TEXT,
                        feature_json TEXT,
                        htf_context_json TEXT,
                        recent_bars_json TEXT,
                        strategy_snapshot_json TEXT,
                        market_snapshot_json TEXT
                    );

                    CREATE TABLE IF NOT EXISTS paper_trades (
                        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        signal_id INTEGER,
                        created_at TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        closed_at TEXT,
                        symbol TEXT,
                        interval TEXT,
                        slot_id INTEGER,
                        strategy_id INTEGER,
                        version_id INTEGER,
                        strategy_name TEXT,
                        version_no INTEGER,
                        side TEXT,
                        entry_price REAL,
                        stop_loss REAL,
                        stop_loss_initial REAL,
                        stop_loss_current REAL,
                        sl_state TEXT,
                        take_profit REAL,
                        expected_rr TEXT,
                        tp_mode TEXT,
                        tp_count INTEGER,
                        tp_late_trigger_ratio REAL,
                        late_trigger_index INTEGER,
                        tp_levels_json TEXT,
                        tp_hits_json TEXT,
                        tp1_price REAL,
                        tp2_price REAL,
                        tp3_price REAL,
                        tp4_price REAL,
                        tp1_hit_at TEXT,
                        tp2_hit_at TEXT,
                        tp3_hit_at TEXT,
                        tp4_hit_at TEXT,
                        highest_tp_hit INTEGER,
                        tp_hit_count INTEGER,
                        risk_pct REAL,
                        reward_pct REAL,
                        confidence REAL,
                        start_score REAL,
                        decision TEXT,
                        user_comment TEXT,
                        status TEXT,
                        close_reason TEXT,
                        close_price REAL,
                        outcome_label TEXT,
                        mfe_pct REAL,
                        mae_pct REAL,
                        pnl_pct REAL,
                        follow_through_score REAL,
                        setup_summary TEXT,
                        trade_summary TEXT,
                        outcome_summary TEXT,
                        feature_json TEXT,
                        htf_context_json TEXT,
                        recent_bars_json TEXT,
                        strategy_snapshot_json TEXT,
                        live_score REAL,
                        live_bias TEXT,
                        last_price REAL,
                        manual_flag INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_signal_id ON paper_trades(signal_id) WHERE signal_id IS NOT NULL;
                    """
                )
                con.commit()
                self._migrate_schema(con)
                con.commit()
            finally:
                con.close()


    def _migrate_schema(self, con: sqlite3.Connection) -> None:
        expected_cols = {
            "signal_events": {
                "strategy_mode": "TEXT DEFAULT 'single'",
                "trade_owner_key": "TEXT",
                "exit_family": "TEXT",
                "bundle_components_json": "TEXT"
            },
            "paper_trades": {
                "strategy_mode": "TEXT DEFAULT 'single'",
                "trade_owner_key": "TEXT",
                "exit_family": "TEXT",
                "be_trigger_index": "INTEGER",
                "lock_trigger_index": "INTEGER",
                "lock_to_tp_index": "INTEGER",
                "bundle_components_json": "TEXT",
                "stop_loss_initial": "REAL",
                "stop_loss_current": "REAL",
                "sl_state": "TEXT",
                "tp_mode": "TEXT",
                "tp_count": "INTEGER",
                "tp_late_trigger_ratio": "REAL",
                "late_trigger_index": "INTEGER",
                "tp_levels_json": "TEXT",
                "tp_hits_json": "TEXT",
                "tp1_price": "REAL",
                "tp2_price": "REAL",
                "tp3_price": "REAL",
                "tp4_price": "REAL",
                "tp1_hit_at": "TEXT",
                "tp2_hit_at": "TEXT",
                "tp3_hit_at": "TEXT",
                "tp4_hit_at": "TEXT",
                "highest_tp_hit": "INTEGER DEFAULT 0",
                "tp_hit_count": "INTEGER DEFAULT 0"
            }
        }
        for table, cols in expected_cols.items():
            existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, ddl in cols.items():
                if name not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _seed_defaults_if_needed(self) -> None:
        root = Path(__file__).resolve().parent.parent
        bundled = root / "bundled_strategies"
        with self._lock:
            con = self._connect()
            try:
                strategy_count = con.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
                slot_count = con.execute("SELECT COUNT(*) FROM strategy_slots").fetchone()[0]
                if strategy_count == 0 and bundled.exists():
                    for path in sorted(bundled.glob("*.json")):
                        try:
                            payload = json.loads(path.read_text(encoding="utf-8"))
                            strategy = payload.get("strategy", payload)
                            self.create_strategy(strategy.get("strategy_name", path.stem), strategy.get("template_key", "rule_builder"), strategy)
                        except Exception:
                            continue
                if slot_count == 0:
                    latest = self.get_latest_strategy_versions()
                    latest_ids = latest["version_id"].tolist() if not latest.empty else []
                    for slot_id in range(1, STRATEGY_SLOT_COUNT + 1):
                        version_id = int(latest_ids[slot_id - 1]) if slot_id <= len(latest_ids) else None
                        con.execute(
                            "INSERT OR REPLACE INTO strategy_slots (slot_id, version_id, analyze_flag, enabled, updated_at) VALUES (?, ?, ?, ?, ?)",
                            [slot_id, version_id, 1 if version_id else 0, 1 if version_id else 0, _now_iso()],
                        )
                con.commit()
            finally:
                con.close()

    # ---------- runtime snapshot helpers ----------
    def read_market_snapshot(self) -> dict[str, Any]:
        return read_json(self.market_snapshot_path, {"symbols": {}, "updated_at": None, "poll_seconds": 300, "timeframe": "5m"})

    def write_market_snapshot(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.market_snapshot_path, payload)

    def get_market_snapshot(self, symbol: str) -> dict[str, Any]:
        snap = self.read_market_snapshot()
        return dict(((snap.get("symbols") or {}).get(symbol.upper()) or {}).get("market_snapshot") or {})

    def get_symbol_rule(self, symbol: str) -> dict[str, Any]:
        snap = self.read_market_snapshot()
        return dict(((snap.get("symbols") or {}).get(symbol.upper()) or {}).get("symbol_rule") or {})

    def get_htf_frames(self, symbol: str, main_timeframe: str) -> dict[str, pd.DataFrame]:
        snap = self.read_market_snapshot()
        symbol_data = ((snap.get("symbols") or {}).get(symbol.upper()) or {})
        out: dict[str, pd.DataFrame] = {}
        for tf, rows in (symbol_data.get("frames") or {}).items():
            if tf == main_timeframe:
                continue
            if not rows:
                continue
            out[tf] = self._rows_to_df(rows)
        # Warm-start fallback from partitioned parquet store.
        for tf in ["15m", "1h", "4h", "1d"]:
            if tf == main_timeframe or tf in out:
                continue
            fallback = load_recent_candles(symbol, tf, limit=400)
            if not fallback.empty:
                out[tf] = fallback
        return out

    def get_candles(self, symbol: str, interval: str, limit: int = 400) -> pd.DataFrame:
        snap = self.read_market_snapshot()
        symbol_data = ((snap.get("symbols") or {}).get(symbol.upper()) or {})
        live_rows = ((symbol_data.get("frames") or {}).get(interval) or [])[-max(limit, 600):]
        live_df = self._rows_to_df(live_rows)
        if len(live_df) >= limit:
            return live_df.tail(limit).reset_index(drop=True)
        fallback_df = load_recent_candles(symbol, interval, limit=max(limit, 800))
        if fallback_df.empty:
            return live_df.tail(limit).reset_index(drop=True)
        if live_df.empty:
            return fallback_df.tail(limit).reset_index(drop=True)
        merged = pd.concat([fallback_df, live_df], ignore_index=True)
        merged = merged.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
        return merged.tail(limit).reset_index(drop=True)

    def _rows_to_df(self, rows: list[dict[str, Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "is_closed"])
        df = pd.DataFrame(rows)
        for col in ["open_time", "close_time"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "is_closed" in df.columns:
            df["is_closed"] = df["is_closed"].astype(bool)
        return df.sort_values("open_time").reset_index(drop=True)

    def get_latest_closed_candle_signature(self, symbols: list[str], interval: str) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for symbol in symbols:
            df = self.get_candles(symbol, interval, limit=10)
            if df.empty:
                out[symbol] = None
                continue
            closed = df[df.get("is_closed", True)] if "is_closed" in df.columns else df
            if closed.empty:
                out[symbol] = None
            else:
                out[symbol] = closed.iloc[-1]["open_time"].isoformat() if pd.notna(closed.iloc[-1]["open_time"]) else None
        return out

    def count_candles(self) -> int:
        snap = self.read_market_snapshot()
        total = 0
        for symbol_data in (snap.get("symbols") or {}).values():
            for rows in (symbol_data.get("frames") or {}).values():
                total += len(rows or [])
        return total

    def upsert_symbol_rule(self, rule: dict[str, Any]) -> None:
        symbol = str(rule.get("symbol") or "").upper()
        if not symbol:
            return
        snap = self.read_market_snapshot()
        snap.setdefault("symbols", {}).setdefault(symbol, {"frames": {}, "market_snapshot": {}, "symbol_rule": {}})
        snap["symbols"][symbol]["symbol_rule"] = rule
        self.write_market_snapshot(snap)

    def upsert_candles(self, candles: list[dict[str, Any]]) -> None:
        # compatibility shim; collector now writes full snapshots atomically
        if not candles:
            return
        snap = self.read_market_snapshot()
        for candle in candles:
            symbol = str(candle.get("symbol") or "").upper()
            interval = str(candle.get("interval") or "")
            if not symbol or not interval:
                continue
            snap.setdefault("symbols", {}).setdefault(symbol, {"frames": {}, "market_snapshot": {}, "symbol_rule": {}})
            rows = list((snap["symbols"][symbol].setdefault("frames", {}).get(interval) or []))
            serial = self._serialize_candle_row(candle)
            key = serial.get("open_time")
            replaced = False
            for idx, row in enumerate(rows):
                if row.get("open_time") == key:
                    rows[idx] = serial
                    replaced = True
                    break
            if not replaced:
                rows.append(serial)
            rows = sorted(rows, key=lambda x: x.get("open_time") or "")[-600:]
            snap["symbols"][symbol]["frames"][interval] = rows
        self.write_market_snapshot(snap)

    def _serialize_candle_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        for key in ["open_time", "close_time"]:
            if key in out and hasattr(out[key], "isoformat"):
                out[key] = out[key].isoformat()
        return out

    # ---------- analysis cache ----------
    def write_analysis_cache(self, scanner_rows: list[dict[str, Any]], analysis_map: dict[str, Any], meta: dict[str, Any]) -> None:
        payload = {
            "scanner_rows": json.loads(_json_dumps(scanner_rows)),
            "analysis_map": json.loads(_json_dumps(_prepare_analysis_map_for_cache(analysis_map))),
            "meta": json.loads(_json_dumps(meta)),
        }
        atomic_write_json(self.analysis_cache_path, payload)

    def read_analysis_cache(self) -> dict[str, Any]:
        default = {"scanner_rows": [], "analysis_map": {}, "meta": {}}
        cache = read_json(self.analysis_cache_path, default)
        if isinstance(cache, dict):
            cache["analysis_map"] = _restore_analysis_map_from_cache(cache.get("analysis_map", {}))
        return cache

    # ---------- strategy library ----------
    def get_strategy_library(self) -> pd.DataFrame:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT s.strategy_id, s.strategy_name, s.template_key, s.retired,
                           sv.version_id, sv.version_no, sv.is_active, sv.created_at,
                           sv.expected_rr, sv.score_threshold, sv.human_thesis, sv.expected_outcome,
                           sv.indicator_description, sv.indicators_json, sv.indicator_rules_json,
                           sv.rule_params_json, sv.notes
                    FROM strategies s
                    JOIN strategy_versions sv ON sv.strategy_id = s.strategy_id
                    WHERE s.retired = 0
                    ORDER BY s.strategy_id, sv.version_no DESC
                    """
                ).fetchall()
                return pd.DataFrame([dict(r) for r in rows])
            finally:
                con.close()

    def get_latest_strategy_versions(self) -> pd.DataFrame:
        df = self.get_strategy_library()
        if df.empty:
            return df
        return df.sort_values(["strategy_id", "version_no"], ascending=[True, False]).drop_duplicates(["strategy_id"]).reset_index(drop=True)

    def create_strategy(self, strategy_name: str, template_key: str, payload: dict[str, Any]) -> int:
        with self._lock:
            con = self._connect()
            try:
                next_id = con.execute("SELECT COALESCE(MAX(strategy_id), 0) + 1 FROM strategies").fetchone()[0]
                con.execute("INSERT INTO strategies (strategy_id, strategy_name, template_key, retired) VALUES (?, ?, ?, 0)", [next_id, strategy_name, template_key])
                self._insert_strategy_version(con, int(next_id), payload)
                con.commit()
                return int(next_id)
            finally:
                con.close()

    def save_strategy_version(self, strategy_id: int, payload: dict[str, Any]) -> int:
        with self._lock:
            con = self._connect()
            try:
                version_id = self._insert_strategy_version(con, strategy_id, payload)
                con.execute("UPDATE strategies SET strategy_name = ?, template_key = ? WHERE strategy_id = ?", [payload.get("strategy_name", ""), payload.get("template_key", "rule_builder"), strategy_id])
                con.commit()
                return version_id
            finally:
                con.close()

    def _insert_strategy_version(self, con: sqlite3.Connection, strategy_id: int, payload: dict[str, Any]) -> int:
        version_no = int(con.execute("SELECT COALESCE(MAX(version_no), 0) + 1 FROM strategy_versions WHERE strategy_id = ?", [strategy_id]).fetchone()[0])
        version_id = int(con.execute("SELECT COALESCE(MAX(version_id), 0) + 1 FROM strategy_versions").fetchone()[0])
        con.execute(
            """
            INSERT INTO strategy_versions (
                version_id, strategy_id, version_no, is_active, created_at,
                human_thesis, expected_outcome, indicator_description,
                indicators_json, indicator_rules_json, rule_params_json,
                expected_rr, score_threshold, notes
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                version_id,
                strategy_id,
                version_no,
                _now_iso(),
                payload.get("human_thesis", ""),
                payload.get("expected_outcome", ""),
                payload.get("indicator_description", ""),
                _json_dumps(payload.get("indicators", [])),
                _json_dumps(payload.get("indicator_rules", [])),
                _json_dumps(payload.get("rule_params", {})),
                payload.get("expected_rr", "1:4"),
                float(payload.get("score_threshold", 70)),
                payload.get("notes", ""),
            ],
        )
        return int(version_id)

    # ---------- strategy slots ----------
    def get_active_slots(self) -> pd.DataFrame:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT ss.slot_id, ss.version_id, ss.analyze_flag AS analyze, ss.enabled,
                           s.strategy_name, s.template_key, sv.version_no,
                           sv.created_at AS version_created_at, sv.expected_rr, sv.score_threshold,
                           sv.human_thesis, sv.expected_outcome, sv.indicator_description,
                           sv.indicators_json, sv.indicator_rules_json, sv.rule_params_json,
                           sv.notes, s.strategy_id
                    FROM strategy_slots ss
                    LEFT JOIN strategy_versions sv ON sv.version_id = ss.version_id
                    LEFT JOIN strategies s ON s.strategy_id = sv.strategy_id
                    ORDER BY ss.slot_id
                    """
                ).fetchall()
                return pd.DataFrame([dict(r) for r in rows])
            finally:
                con.close()

    def save_slot(self, slot_id: int, version_id: int | None, analyze: bool, enabled: bool) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "INSERT INTO strategy_slots (slot_id, version_id, analyze_flag, enabled, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(slot_id) DO UPDATE SET version_id=excluded.version_id, analyze_flag=excluded.analyze_flag, enabled=excluded.enabled, updated_at=excluded.updated_at",
                    [slot_id, version_id, _coerce_bool(analyze), _coerce_bool(enabled), _now_iso()],
                )
                con.commit()
            finally:
                con.close()

    def set_all_slots_enabled(self, enabled: bool = True, analyze: bool = True) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute("UPDATE strategy_slots SET analyze_flag = ?, enabled = ?, updated_at = ? WHERE version_id IS NOT NULL", [_coerce_bool(analyze or enabled), _coerce_bool(enabled), _now_iso()])
                con.commit()
            finally:
                con.close()

    # ---------- signals ----------
    def upsert_signal(self, payload: dict[str, Any]) -> int:
        with self._lock:
            con = self._connect()
            try:
                existing = con.execute("SELECT signal_id FROM signal_events WHERE signal_key = ?", [payload["signal_key"]]).fetchone()
                common_values = [
                    payload.get("strategy_mode", "single"),
                    payload.get("trade_owner_key"),
                    payload.get("exit_family"),
                    _json_dumps(payload.get("bundle_components_json", payload.get("bundle_components", []))),
                    payload.get("score"),
                    payload.get("recommendation"),
                    payload.get("setup_summary"),
                    _json_dumps(payload.get("feature_json", {})),
                    _json_dumps(payload.get("htf_context_json", {})),
                    _json_dumps(payload.get("recent_bars_json", [])),
                    _json_dumps(payload.get("strategy_snapshot_json", {})),
                    _json_dumps(payload.get("market_snapshot_json", {})),
                ]
                if existing:
                    signal_id = int(existing[0])
                    con.execute(
                        """
                        UPDATE signal_events
                        SET strategy_mode=?, trade_owner_key=?, exit_family=?, bundle_components_json=?,
                            score=?, recommendation=?, setup_summary=?, feature_json=?, htf_context_json=?,
                            recent_bars_json=?, strategy_snapshot_json=?, market_snapshot_json=?
                        WHERE signal_id=?
                        """,
                        common_values + [signal_id],
                    )
                else:
                    cur = con.execute(
                        """
                        INSERT INTO signal_events (
                            signal_key, created_at, symbol, interval, bar_open_time, slot_id, strategy_id, version_id,
                            strategy_name, version_no, strategy_mode, trade_owner_key, exit_family, bundle_components_json,
                            regime, bias, score, recommendation, setup_summary,
                            feature_json, htf_context_json, recent_bars_json, strategy_snapshot_json, market_snapshot_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            payload.get("signal_key"), _now_iso(), payload.get("symbol"), payload.get("interval"), str(payload.get("bar_open_time")),
                            payload.get("slot_id"), payload.get("strategy_id"), payload.get("version_id"), payload.get("strategy_name"), payload.get("version_no"),
                            payload.get("strategy_mode", "single"), payload.get("trade_owner_key"), payload.get("exit_family"), _json_dumps(payload.get("bundle_components_json", payload.get("bundle_components", []))),
                            payload.get("regime"), payload.get("bias"), payload.get("score"), payload.get("recommendation"), payload.get("setup_summary"),
                            _json_dumps(payload.get("feature_json", {})), _json_dumps(payload.get("htf_context_json", {})), _json_dumps(payload.get("recent_bars_json", [])),
                            _json_dumps(payload.get("strategy_snapshot_json", {})), _json_dumps(payload.get("market_snapshot_json", {})),
                        ],
                    )
                    signal_id = int(cur.lastrowid)
                con.commit()
                return signal_id
            finally:
                con.close()

    def get_signal_events(self, limit: int = 200) -> pd.DataFrame:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute("SELECT * FROM signal_events ORDER BY signal_id DESC LIMIT ?", [limit]).fetchall()
                return pd.DataFrame([dict(r) for r in rows])
            finally:
                con.close()

    def has_open_trade_owner_conflict(self, symbol: str, side: str, trade_owner_key: str | None, strategy_mode: str = "single", version_id: int | None = None) -> bool:
        """Conflict key used by live execution: symbol + direction + single/bundle owner."""
        with self._lock:
            con = self._connect()
            try:
                params: list[Any] = [symbol, side, strategy_mode, trade_owner_key]
                row = con.execute(
                    """
                    SELECT 1 FROM paper_trades
                    WHERE symbol = ? AND side = ? AND status = 'OPEN'
                      AND COALESCE(strategy_mode, 'single') = ?
                      AND trade_owner_key = ?
                    LIMIT 1
                    """,
                    params,
                ).fetchone() if trade_owner_key else None
                if row is not None:
                    return True
                # Backwards compatibility for old single-strategy rows that predate trade_owner_key.
                if strategy_mode == "single" and version_id is not None:
                    row = con.execute(
                        "SELECT 1 FROM paper_trades WHERE symbol = ? AND version_id = ? AND side = ? AND status = 'OPEN' LIMIT 1",
                        [symbol, int(version_id), side],
                    ).fetchone()
                    return row is not None
                return False
            finally:
                con.close()

    def has_open_trade_conflict(self, symbol: str, version_id: int, side: str) -> bool:
        return self.has_open_trade_owner_conflict(symbol, side, f"single:{version_id}", "single", version_id=version_id)

    # ---------- trades ----------
    def create_or_update_paper_trade_from_signal(self, payload: dict[str, Any]) -> int:
        def trade_values() -> dict[str, Any]:
            return {
                "signal_id": payload.get("signal_id"),
                "created_at": _now_iso(),
                "opened_at": _now_iso(),
                "symbol": payload.get("symbol"),
                "interval": payload.get("interval"),
                "slot_id": payload.get("slot_id"),
                "strategy_id": payload.get("strategy_id"),
                "version_id": payload.get("version_id"),
                "strategy_name": payload.get("strategy_name"),
                "version_no": payload.get("version_no"),
                "strategy_mode": payload.get("strategy_mode", "single"),
                "trade_owner_key": payload.get("trade_owner_key") or (f"single:{payload.get('version_id')}" if payload.get("version_id") is not None else None),
                "exit_family": payload.get("exit_family"),
                "bundle_components_json": _json_dumps(payload.get("bundle_components_json", payload.get("bundle_components", []))),
                "side": payload.get("side"),
                "entry_price": payload.get("entry_price"),
                "stop_loss": payload.get("stop_loss"),
                "stop_loss_initial": payload.get("stop_loss_initial", payload.get("stop_loss")),
                "stop_loss_current": payload.get("stop_loss_current", payload.get("stop_loss")),
                "sl_state": payload.get("sl_state", "INITIAL"),
                "take_profit": payload.get("take_profit"),
                "expected_rr": payload.get("expected_rr"),
                "tp_mode": payload.get("tp_mode", "structure_atr"),
                "tp_count": payload.get("tp_count"),
                "tp_late_trigger_ratio": payload.get("tp_late_trigger_ratio"),
                "late_trigger_index": payload.get("late_trigger_index"),
                "be_trigger_index": payload.get("be_trigger_index"),
                "lock_trigger_index": payload.get("lock_trigger_index"),
                "lock_to_tp_index": payload.get("lock_to_tp_index"),
                "tp_levels_json": _json_dumps(payload.get("tp_levels", [])),
                "tp_hits_json": _json_dumps(payload.get("tp_hits_json", {})),
                "tp1_price": payload.get("tp1_price"),
                "tp2_price": payload.get("tp2_price"),
                "tp3_price": payload.get("tp3_price"),
                "tp4_price": payload.get("tp4_price"),
                "highest_tp_hit": payload.get("highest_tp_hit", 0),
                "tp_hit_count": payload.get("tp_hit_count", 0),
                "risk_pct": payload.get("risk_pct"),
                "reward_pct": payload.get("reward_pct"),
                "confidence": payload.get("confidence"),
                "start_score": payload.get("confidence"),
                "decision": payload.get("decision", "SKIPPED"),
                "user_comment": payload.get("user_comment", ""),
                "status": "OPEN",
                "setup_summary": payload.get("setup_summary"),
                "trade_summary": payload.get("trade_summary"),
                "feature_json": _json_dumps(payload.get("feature_json", {})),
                "htf_context_json": _json_dumps(payload.get("htf_context_json", {})),
                "recent_bars_json": _json_dumps(payload.get("recent_bars_json", [])),
                "strategy_snapshot_json": _json_dumps(payload.get("strategy_snapshot_json", {})),
                "live_score": payload.get("confidence"),
                "live_bias": payload.get("side"),
                "last_price": payload.get("entry_price"),
                "manual_flag": 0,
            }

        with self._lock:
            con = self._connect()
            try:
                existing = con.execute("SELECT trade_id FROM paper_trades WHERE signal_id = ?", [payload.get("signal_id")]).fetchone() if payload.get("signal_id") else None
                values = trade_values()
                if existing:
                    trade_id = int(existing[0])
                    update_cols = [
                        "strategy_mode", "trade_owner_key", "exit_family", "bundle_components_json",
                        "live_score", "live_bias", "last_price", "feature_json", "htf_context_json",
                        "recent_bars_json", "strategy_snapshot_json", "tp_levels_json",
                        "tp1_price", "tp2_price", "tp3_price", "tp4_price",
                        "stop_loss_current", "sl_state", "tp_mode", "tp_count",
                        "tp_late_trigger_ratio", "late_trigger_index", "be_trigger_index",
                        "lock_trigger_index", "lock_to_tp_index",
                    ]
                    set_clause = ", ".join([f"{col}=?" for col in update_cols])
                    con.execute(f"UPDATE paper_trades SET {set_clause} WHERE trade_id=?", [values.get(col) for col in update_cols] + [trade_id])
                else:
                    cols = list(values.keys())
                    placeholders = ", ".join(["?"] * len(cols))
                    col_sql = ", ".join(cols)
                    cur = con.execute(f"INSERT INTO paper_trades ({col_sql}) VALUES ({placeholders})", [values.get(col) for col in cols])
                    trade_id = int(cur.lastrowid)
                con.commit()
                return trade_id
            finally:
                con.close()

    def create_manual_trade(self, payload: dict[str, Any]) -> int:
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    """
                    INSERT INTO paper_trades (
                        signal_id, created_at, opened_at, symbol, interval, slot_id, strategy_id, version_id, strategy_name, version_no,
                        side, entry_price, stop_loss, stop_loss_initial, stop_loss_current, sl_state, take_profit, expected_rr, tp_mode, tp_count,
                        tp_late_trigger_ratio, late_trigger_index, tp_levels_json, tp_hits_json, tp1_price, tp2_price, tp3_price, tp4_price,
                        highest_tp_hit, tp_hit_count, risk_pct, reward_pct, confidence, start_score,
                        decision, user_comment, status, setup_summary, trade_summary, feature_json, htf_context_json, recent_bars_json,
                        strategy_snapshot_json, live_score, live_bias, last_price, manual_flag
                    ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    [
                        _now_iso(), _now_iso(), payload.get("symbol"), payload.get("interval"), payload.get("slot_id"), payload.get("strategy_id"), payload.get("version_id"), payload.get("strategy_name"), payload.get("version_no"),
                        payload.get("side"), payload.get("entry_price"), payload.get("stop_loss"), payload.get("stop_loss_initial", payload.get("stop_loss")), payload.get("stop_loss_current", payload.get("stop_loss")), payload.get("sl_state", "INITIAL"), payload.get("take_profit"), payload.get("expected_rr"), payload.get("tp_mode", "structure_atr"), payload.get("tp_count"),
                        payload.get("tp_late_trigger_ratio"), payload.get("late_trigger_index"), _json_dumps(payload.get("tp_levels", [])), _json_dumps(payload.get("tp_hits_json", {})), payload.get("tp1_price"), payload.get("tp2_price"), payload.get("tp3_price"), payload.get("tp4_price"),
                        payload.get("highest_tp_hit", 0), payload.get("tp_hit_count", 0), payload.get("risk_pct"), payload.get("reward_pct"), payload.get("confidence", 0.0), payload.get("confidence", 0.0),
                        payload.get("decision", "TOOK"), payload.get("user_comment", ""), payload.get("setup_summary", "Manual trade"), payload.get("trade_summary", "Manual trade"),
                        _json_dumps(payload.get("feature_json", {})), _json_dumps(payload.get("htf_context_json", {})), _json_dumps(payload.get("recent_bars_json", [])), _json_dumps(payload.get("strategy_snapshot_json", {})),
                        payload.get("confidence", 0.0), payload.get("side"), payload.get("entry_price"),
                    ],
                )
                con.commit()
                return int(cur.lastrowid)
            finally:
                con.close()

    def get_open_paper_trades(self) -> pd.DataFrame:
        return self._query_df("SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY trade_id DESC")

    def get_paper_trades(self, limit: int = 200) -> pd.DataFrame:
        return self._query_df("SELECT * FROM paper_trades ORDER BY trade_id DESC LIMIT ?", [limit])

    def update_trade_user_fields(self, trade_id: int, decision: str, user_comment: str, entry_price: float, stop_loss: float, take_profit: float, expected_rr: str, risk_pct: float, reward_pct: float) -> None:
        with self._lock:
            con = self._connect()
            try:
                trade_summary = f"Decision: {decision}\nEntry: {entry_price}\nSL: {stop_loss}\nTP: {take_profit}\nExpected RR: {expected_rr}\nComment: {user_comment or ''}"
                con.execute(
                    "UPDATE paper_trades SET decision=?, user_comment=?, entry_price=?, stop_loss=?, take_profit=?, expected_rr=?, risk_pct=?, reward_pct=?, trade_summary=? WHERE trade_id=?",
                    [decision, user_comment, float(entry_price), float(stop_loss), float(take_profit), expected_rr, float(risk_pct), float(reward_pct), trade_summary, trade_id],
                )
                con.commit()
            finally:
                con.close()

    def mark_trade_outcome(self, trade_id: int, status: str, close_reason: str | None, close_price: float, outcome_label: str, mfe_pct: float, mae_pct: float, pnl_pct: float, follow_through_score: float, outcome_summary: str, progress: dict[str, Any] | None = None) -> None:
        with self._lock:
            con = self._connect()
            try:
                progress = progress or {}
                con.execute(
                    "UPDATE paper_trades SET status=?, closed_at=?, close_reason=?, close_price=?, outcome_label=?, mfe_pct=?, mae_pct=?, pnl_pct=?, follow_through_score=?, outcome_summary=?, stop_loss_current=?, sl_state=?, highest_tp_hit=?, tp_hit_count=?, tp_hits_json=?, tp1_hit_at=?, tp2_hit_at=?, tp3_hit_at=?, tp4_hit_at=?, last_price=? WHERE trade_id=?",
                    [status, _now_iso(), close_reason, close_price, outcome_label, mfe_pct, mae_pct, pnl_pct, follow_through_score, outcome_summary, progress.get('stop_loss_current'), progress.get('sl_state'), progress.get('highest_tp_hit'), progress.get('tp_hit_count'), _json_dumps(progress.get('tp_hits_json', {})), progress.get('tp1_hit_at'), progress.get('tp2_hit_at'), progress.get('tp3_hit_at'), progress.get('tp4_hit_at'), progress.get('last_price'), trade_id],
                )
                con.commit()
            finally:
                con.close()

    def manual_close_trade(self, trade_id: int, close_price: float) -> None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute("SELECT trade_id, side, entry_price FROM paper_trades WHERE trade_id=?", [trade_id]).fetchone()
                if not row:
                    return
                side = row["side"]
                entry = float(row["entry_price"])
                pnl_pct = ((close_price / entry) - 1.0) * (100 if side == "LONG" else -100)
                con.execute(
                    "UPDATE paper_trades SET status='CLOSED', closed_at=?, close_reason='MANUAL', close_price=?, outcome_label='MANUAL_CLOSE', pnl_pct=?, outcome_summary=? WHERE trade_id=?",
                    [_now_iso(), close_price, pnl_pct, f"Manual close at {close_price}. PnL %: {pnl_pct:.4f}", trade_id],
                )
                con.commit()
            finally:
                con.close()

    def update_open_trade_live_scores(self, score_map: dict[tuple[Any, ...], dict[str, Any]]) -> None:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute("SELECT trade_id, symbol, version_id, trade_owner_key, strategy_mode, status FROM paper_trades WHERE status='OPEN'").fetchall()
                for row in rows:
                    symbol = str(row["symbol"])
                    owner_key = row["trade_owner_key"]
                    version_id = row["version_id"]
                    state = {}
                    if owner_key:
                        state = score_map.get((symbol, owner_key), {}) or score_map.get((symbol, str(owner_key)), {})
                    if not state and version_id is not None:
                        try:
                            state = score_map.get((symbol, int(version_id)), {})
                        except Exception:
                            state = {}
                    con.execute("UPDATE paper_trades SET live_score=?, live_bias=?, last_price=? WHERE trade_id=?", [state.get("score"), state.get("bias"), state.get("last_price"), row["trade_id"]])
                con.commit()
            finally:
                con.close()

    def update_trade_progress(self, trade_id: int, progress: dict[str, Any]) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "UPDATE paper_trades SET stop_loss_current=?, sl_state=?, highest_tp_hit=?, tp_hit_count=?, tp_hits_json=?, tp1_hit_at=?, tp2_hit_at=?, tp3_hit_at=?, tp4_hit_at=?, last_price=? WHERE trade_id=?",
                    [progress.get('stop_loss_current'), progress.get('sl_state'), progress.get('highest_tp_hit'), progress.get('tp_hit_count'), _json_dumps(progress.get('tp_hits_json', {})), progress.get('tp1_hit_at'), progress.get('tp2_hit_at'), progress.get('tp3_hit_at'), progress.get('tp4_hit_at'), progress.get('last_price'), trade_id],
                )
                con.commit()
            finally:
                con.close()

    def _query_df(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(sql, params or [])
                rows = cur.fetchall()
                return pd.DataFrame([dict(r) for r in rows])
            finally:
                con.close()
