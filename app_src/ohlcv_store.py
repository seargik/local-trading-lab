from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import OHLCV_STORE_ROOT

COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "is_closed"]


def timeframe_to_pandas_rule(tf: str) -> str:
    tf = str(tf).strip().lower()
    m = re.fullmatch(r"(\d+)([mhdw])", tf)
    if not m:
        raise ValueError(f"Unsupported timeframe: {tf}")
    num, unit = m.groups()
    unit_map = {"m": "min", "h": "h", "d": "D", "w": "W"}
    return f"{int(num)}{unit_map[unit]}"


def standardize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename_map = {
        "open time": "open_time",
        "timestamp": "open_time",
        "date": "open_time",
        "Open time": "open_time",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
        "close time": "close_time",
        "Close time": "close_time",
    }
    frame = frame.rename(columns=rename_map)
    for required in ["open_time", "open", "high", "low", "close", "volume"]:
        if required not in frame.columns:
            raise ValueError(f"Missing required OHLCV column: {required}")
    if pd.api.types.is_numeric_dtype(frame["open_time"]):
        frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True, errors="coerce")
    else:
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    if "close_time" in frame.columns:
        if pd.api.types.is_numeric_dtype(frame["close_time"]):
            frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True, errors="coerce")
        else:
            frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True, errors="coerce")
    else:
        frame["close_time"] = frame["open_time"]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "is_closed" not in frame.columns:
        frame["is_closed"] = True
    frame["is_closed"] = frame["is_closed"].fillna(True).astype(bool)
    frame = frame[COLUMNS].dropna(subset=["open_time", "open", "high", "low", "close"])
    return frame.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    rule = timeframe_to_pandas_rule(timeframe)
    frame = standardize_ohlcv(df).sort_values("open_time").set_index("open_time")
    resampled = pd.DataFrame({
        "open": frame["open"].resample(rule, label="left", closed="left").first(),
        "high": frame["high"].resample(rule, label="left", closed="left").max(),
        "low": frame["low"].resample(rule, label="left", closed="left").min(),
        "close": frame["close"].resample(rule, label="left", closed="left").last(),
        "volume": frame["volume"].resample(rule, label="left", closed="left").sum(),
    }).dropna(subset=["open", "high", "low", "close"]).reset_index()
    if len(resampled) >= 2:
        resampled["close_time"] = resampled["open_time"].shift(-1).fillna(resampled["open_time"])
    else:
        resampled["close_time"] = resampled["open_time"]
    resampled["is_closed"] = True
    return resampled[COLUMNS]


def _store_root(store_root: str | Path | None = None) -> Path:
    return Path(store_root or OHLCV_STORE_ROOT)


def month_partition_path(symbol: str, timeframe: str, year: int, month: int, store_root: str | Path | None = None) -> Path:
    root = _store_root(store_root)
    return root / f"symbol={symbol.upper()}" / f"timeframe={timeframe}" / f"year={year:04d}" / f"month={month:02d}" / "candles.parquet"


def _partition_files(symbol: str, timeframe: str, store_root: str | Path | None = None) -> list[Path]:
    root = _store_root(store_root) / f"symbol={symbol.upper()}" / f"timeframe={timeframe}"
    if not root.exists():
        return []
    return sorted(root.glob("year=*/month=*/candles.parquet"))


def _extract_year_month(path: Path) -> tuple[int, int] | None:
    year = month = None
    for part in path.parts:
        if part.startswith("year="):
            try:
                year = int(part.split("=", 1)[1])
            except Exception:
                year = None
        elif part.startswith("month="):
            try:
                month = int(part.split("=", 1)[1])
            except Exception:
                month = None
    return (year, month) if year and month else None


def append_candles(candles: list[dict[str, Any]], store_root: str | Path | None = None) -> list[dict[str, Any]]:
    if not candles:
        return []
    grouped: dict[tuple[str, str, int, int], list[dict[str, Any]]] = {}
    for row in candles:
        symbol = str(row.get("symbol") or "").upper().strip()
        timeframe = str(row.get("interval") or row.get("timeframe") or "").strip()
        if not symbol or not timeframe:
            continue
        open_time = pd.to_datetime(row.get("open_time"), utc=True, errors="coerce")
        if pd.isna(open_time):
            continue
        grouped.setdefault((symbol, timeframe, int(open_time.year), int(open_time.month)), []).append(row)
    written: list[dict[str, Any]] = []
    for (symbol, timeframe, year, month), rows in grouped.items():
        target = month_partition_path(symbol, timeframe, year, month, store_root=store_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        new_df = standardize_ohlcv(pd.DataFrame(rows))
        if target.exists():
            existing = standardize_ohlcv(pd.read_parquet(target))
            merged = pd.concat([existing, new_df], ignore_index=True)
        else:
            merged = new_df
        merged = merged.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
        merged.to_parquet(target, index=False)
        written.append({"symbol": symbol, "timeframe": timeframe, "rows": int(len(new_df)), "target": str(target)})
    return written


def load_recent_candles(symbol: str, timeframe: str, limit: int = 400, store_root: str | Path | None = None) -> pd.DataFrame:
    symbol = symbol.upper()
    files = _partition_files(symbol, timeframe, store_root=store_root)
    frames: list[pd.DataFrame] = []
    for path in reversed(files):
        try:
            frames.append(standardize_ohlcv(pd.read_parquet(path)))
        except Exception:
            continue
        if sum(len(x) for x in frames) >= limit * 2:
            break
    if frames:
        merged = pd.concat(list(reversed(frames)), ignore_index=True)
        merged = merged.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
        return merged.tail(limit).reset_index(drop=True)
    if timeframe != "5m":
        base = load_recent_candles(symbol, "5m", limit=max(limit * 24, 500), store_root=store_root)
        if not base.empty:
            return resample_ohlcv(base, timeframe).tail(limit).reset_index(drop=True)
    return pd.DataFrame(columns=COLUMNS)


def load_range(symbol: str, timeframe: str, start: Any = None, end: Any = None, limit: int | None = None, store_root: str | Path | None = None) -> pd.DataFrame:
    symbol = symbol.upper()
    start_ts = pd.to_datetime(start, utc=True, errors="coerce") if start is not None else None
    end_ts = pd.to_datetime(end, utc=True, errors="coerce") if end is not None else None
    files = _partition_files(symbol, timeframe, store_root=store_root)
    selected: list[Path] = []
    if files and (start_ts is not None or end_ts is not None):
        for path in files:
            ym = _extract_year_month(path)
            if ym is None:
                selected.append(path)
                continue
            year, month = ym
            month_start = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
            month_end = month_start + pd.offsets.MonthBegin(1)
            if start_ts is not None and month_end <= start_ts:
                continue
            if end_ts is not None and month_start > end_ts:
                continue
            selected.append(path)
    else:
        selected = files

    frames: list[pd.DataFrame] = []
    for path in selected:
        try:
            frames.append(standardize_ohlcv(pd.read_parquet(path)))
        except Exception:
            continue
    if frames:
        merged = pd.concat(frames, ignore_index=True).sort_values("open_time").drop_duplicates(subset=["open_time"], keep="last").reset_index(drop=True)
    elif timeframe != "5m":
        base = load_range(symbol, "5m", start=start_ts, end=end_ts, limit=None, store_root=store_root)
        merged = resample_ohlcv(base, timeframe) if not base.empty else pd.DataFrame(columns=COLUMNS)
    else:
        merged = pd.DataFrame(columns=COLUMNS)
    if start_ts is not None:
        merged = merged[merged["open_time"] >= start_ts]
    if end_ts is not None:
        merged = merged[merged["open_time"] <= end_ts]
    if limit is not None:
        merged = merged.tail(limit)
    return merged.reset_index(drop=True)
