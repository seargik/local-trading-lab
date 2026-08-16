from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from app_src.ohlcv_store import append_candles, month_partition_path, resample_ohlcv, standardize_ohlcv
from app_src.backtest_core import _read_source_table
from app_src.settings import DEFAULT_BOOTSTRAP_SOURCE_ROOT, OHLCV_STORE_ROOT


def infer_symbol_timeframe_date(path: Path) -> tuple[str | None, str | None, str | None]:
    parts = [path.name.upper(), path.stem.upper()] + [p.upper() for p in path.parts[-6:]]
    symbol = timeframe = date_str = None
    for part in parts:
        m = re.search(r"([A-Z0-9]{2,20}USDT)", part)
        if m and symbol is None:
            symbol = m.group(1)
        m = re.search(r"(^|[^0-9A-Z])(1M|3M|5M|15M|30M|1H|2H|4H|6H|8H|12H|1D|1W)([^0-9A-Z]|$)", part)
        if m and timeframe is None:
            timeframe = m.group(2).lower()
        if date_str is None:
            m_day = re.search(r"(20\d{2}-\d{2}-\d{2})", part)
            if m_day:
                date_str = m_day.group(1)
            else:
                m_month = re.search(r"(20\d{2}-\d{2})(?!-\d{2})", part)
                if m_month:
                    date_str = m_month.group(1)
    return symbol, timeframe, date_str


def discover_extracted_files(source_root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".csv", ".parquet"}:
            continue
        symbol, timeframe, _ = infer_symbol_timeframe_date(path)
        if symbol and timeframe:
            out.append(path)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert extracted Binance monthly and daily OHLCV files into a partitioned parquet OHLCV store.")
    parser.add_argument("source_root", nargs="?", default=str(DEFAULT_BOOTSTRAP_SOURCE_ROOT), help="Folder like data_bootstrap/extracted that contains symbol subfolders with monthly and/or daily CSV files")
    parser.add_argument("--store-root", default=str(OHLCV_STORE_ROOT), help="Output folder for partitioned parquet store")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols to include")
    parser.add_argument("--timeframes", default="5m", help="Comma-separated source timeframes to import, usually 5m")
    parser.add_argument("--derive", default="15m,1h,4h", help="Comma-separated derived timeframes to create from 5m after import. Empty string disables.")
    parser.add_argument("--reset-store", action="store_true", help="Delete the existing store root before rebuilding it.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = Path(args.source_root)
    store_root = Path(args.store_root)
    wanted_symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
    wanted_tfs = {s.strip().lower() for s in args.timeframes.split(",") if s.strip()}
    derive_tfs = [s.strip().lower() for s in args.derive.split(",") if s.strip()]

    if args.reset_store and store_root.exists():
        import shutil
        shutil.rmtree(store_root, ignore_errors=True)
    files = discover_extracted_files(source_root)
    if wanted_symbols:
        files = [p for p in files if (infer_symbol_timeframe_date(p)[0] or "").upper() in wanted_symbols]
    if wanted_tfs:
        files = [p for p in files if (infer_symbol_timeframe_date(p)[1] or "").lower() in wanted_tfs]

    print(f"Source root: {source_root}")
    print(f"Store root: {store_root}")
    print(f"Files discovered: {len(files)}")
    if not files:
        print("No matching extracted files found.")
        return

    imported_files = 0
    rows_written = 0
    for path in files:
        symbol, timeframe, _ = infer_symbol_timeframe_date(path)
        try:
            df = standardize_ohlcv(_read_source_table(path))
            payload = []
            for row in df.to_dict(orient="records"):
                row["symbol"] = symbol
                row["interval"] = timeframe
                payload.append(row)
            append_candles(payload, store_root=store_root)
            imported_files += 1
            rows_written += len(df)
        except Exception as exc:
            print(f"SKIP {path}: {exc}")
    print(f"Imported source files: {imported_files}")
    print(f"Base rows written: {rows_written}")

    if derive_tfs:
        symbols = sorted({infer_symbol_timeframe_date(p)[0] for p in files if infer_symbol_timeframe_date(p)[0]})
        for symbol in symbols:
            base_paths = sorted((store_root / f"symbol={symbol}" / "timeframe=5m").glob("year=*/month=*/candles.parquet"))
            if not base_paths:
                continue
            for base_path in base_paths:
                try:
                    base_df = standardize_ohlcv(pd.read_parquet(base_path))
                except Exception:
                    continue
                if base_df.empty:
                    continue
                year = int(next(p.split("=",1)[1] for p in base_path.parts if p.startswith("year=")))
                month = int(next(p.split("=",1)[1] for p in base_path.parts if p.startswith("month=")))
                for tf in derive_tfs:
                    if tf == "5m":
                        continue
                    derived = resample_ohlcv(base_df, tf)
                    if derived.empty:
                        continue
                    target = month_partition_path(symbol, tf, year, month, store_root=store_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    derived.to_parquet(target, index=False)
        print(f"Derived timeframes written: {', '.join(derive_tfs)}")

    print("Done. You can now point the Signal tab warm-start and the Backtest tab History folder to data/ohlcv_store.")


if __name__ == "__main__":
    main()
