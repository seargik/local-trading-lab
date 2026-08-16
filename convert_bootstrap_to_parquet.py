from __future__ import annotations

import argparse
from pathlib import Path

from app_src.backtest_core import convert_bootstrap_to_parquet


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Binance bootstrap CSV files into parquet cache for faster local backtests.")
    parser.add_argument("source_root", nargs="?", default="data_bootstrap", help="Folder containing merged Binance CSV files")
    args = parser.parse_args()
    source = Path(args.source_root)
    converted = convert_bootstrap_to_parquet(source)
    print(f"Converted {len(converted)} symbol/timeframe groups.")
    for item in converted:
        print(f"{item['symbol']} {item['timeframe']}: {item['rows']} rows -> {item['target']}")


if __name__ == "__main__":
    main()
