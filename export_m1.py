"""
Export M1 history from MetaTrader 5 to Parquet with UTC timestamps.

Run on the Windows machine where MT5 is installed and logged in to your broker.

Before running:
    MT5 → Tools → Options → Charts → "Max bars in chart" = Unlimited, then restart MT5.
    pip install MetaTrader5 pandas pyarrow

Usage:
    python export_m1.py --symbol XAUUSD --years 3
    python export_m1.py --symbol XAUUSD.m --from 2023-01-01 --to 2026-10-01 --server-tz ny+7
    python export_m1.py --symbol XAUUSD --server-tz fixed:2

--server-tz modes (how the broker's server clock maps to UTC):
    ny+7       server clock = New York time + 7h (UTC+2 winter / UTC+3 summer). Most gold brokers.
    fixed:<h>  server clock = UTC + h hours, all year.
    auto       detect today's offset from the live tick and apply it to all history (ignores DST).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5
import pandas as pd

NY = ZoneInfo("America/New_York")
COLUMNS = ["time_utc", "open", "high", "low", "close", "tick_volume", "spread"]


def detect_current_offset_hours(symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        sys.exit(f"Cannot read live tick for {symbol}: {mt5.last_error()}")
    diff = tick.time - datetime.now(timezone.utc).timestamp()
    hours = round(diff / 3600)
    if abs(diff - hours * 3600) > 900:
        print(f"WARNING: last tick is stale ({diff / 3600:+.2f}h); market may be closed. "
              f"Detected offset {hours:+d}h may be wrong.")
    return hours


def ny7_offset_now() -> int:
    ny_offset = datetime.now(NY).utcoffset()
    return int(ny_offset.total_seconds() // 3600) + 7


def to_utc(server_naive: pd.Series, mode: str, symbol: str) -> pd.Series:
    if mode == "ny+7":
        ny_local = server_naive - pd.Timedelta(hours=7)
        return ny_local.dt.tz_localize(NY, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    if mode.startswith("fixed:"):
        hours = float(mode.split(":", 1)[1])
        return (server_naive - pd.Timedelta(hours=hours)).dt.tz_localize("UTC")
    if mode == "auto":
        hours = detect_current_offset_hours(symbol)
        print(f"WARNING: applying a single offset ({hours:+d}h) to all history; DST shifts are ignored.")
        return (server_naive - pd.Timedelta(hours=hours)).dt.tz_localize("UTC")
    sys.exit(f"Unknown --server-tz mode: {mode}")


def check_server_tz(mode: str, symbol: str) -> None:
    detected = detect_current_offset_hours(symbol)
    if mode == "ny+7":
        expected = ny7_offset_now()
    elif mode.startswith("fixed:"):
        expected = round(float(mode.split(":", 1)[1]))
    else:
        return
    status = "OK" if detected == expected else "MISMATCH — choose another --server-tz"
    print(f"Server offset now: detected {detected:+d}h, mode '{mode}' expects {expected:+d}h → {status}")


def fetch(symbol: str, start: datetime, end: datetime, chunk_days: int) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, cur, nxt)
        if rates is None:
            print(f"  {cur:%Y-%m-%d} → {nxt:%Y-%m-%d}: error {mt5.last_error()}")
        else:
            print(f"  {cur:%Y-%m-%d} → {nxt:%Y-%m-%d}: {len(rates):>7} bars")
            if len(rates):
                frames.append(pd.DataFrame(rates))
        cur = nxt
    if not frames:
        sys.exit("No data returned. Check the symbol name and the 'Max bars in chart' setting.")
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def report(df: pd.DataFrame, requested_start: datetime) -> None:
    t = df["time_utc"]
    gaps_min = t.diff().dt.total_seconds().div(60)
    midweek = t.dt.weekday.between(1, 3)
    long_gaps = int(((gaps_min > 90) & midweek).sum())
    zero_spread = float((df["spread"] == 0).mean() * 100)
    print("\n── Export summary ──")
    print(f"Rows:            {len(df):,}")
    print(f"From → to (UTC): {t.iloc[0]} → {t.iloc[-1]}")
    print(f"Midweek gaps >90 min: {long_gaps}")
    print(f"Zero-spread bars: {zero_spread:.1f}%")
    if t.iloc[0] > pd.Timestamp(requested_start) + pd.Timedelta(days=7):
        print("WARNING: history starts later than requested. Raise 'Max bars in chart', "
              "scroll the M1 chart back to load history, restart MT5 and re-run.")
    if zero_spread > 5:
        print("WARNING: many bars have zero spread; the backtester must use min_spread from config.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export MT5 M1 bars to Parquet (UTC).")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--server-tz", default="ny+7", help="ny+7 | fixed:<hours> | auto")
    ap.add_argument("--out", default="data/xauusd_m1.parquet")
    ap.add_argument("--chunk-days", type=int, default=30)
    ap.add_argument("--terminal-path", default=None, help="Path to terminal64.exe if several MT5 are installed")
    args = ap.parse_args()

    ok = mt5.initialize(path=args.terminal_path) if args.terminal_path else mt5.initialize()
    if not ok:
        sys.exit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        info = mt5.symbol_info(args.symbol)
        if info is None:
            sys.exit(f"Symbol not found: {args.symbol}")
        if not info.visible and not mt5.symbol_select(args.symbol, True):
            sys.exit(f"Cannot select symbol {args.symbol}")

        end = (datetime.fromisoformat(args.date_to).replace(tzinfo=timezone.utc)
               if args.date_to else datetime.now(timezone.utc))
        start = (datetime.fromisoformat(args.date_from).replace(tzinfo=timezone.utc)
                 if args.date_from else end - timedelta(days=round(365 * args.years)))

        check_server_tz(args.server_tz, args.symbol)
        print(f"Fetching {args.symbol} M1 {start:%Y-%m-%d} → {end:%Y-%m-%d}")
        raw = fetch(args.symbol, start, end, args.chunk_days)

        raw["time_utc"] = to_utc(pd.to_datetime(raw["time"], unit="s"), args.server_tz, args.symbol)
        dropped = int(raw["time_utc"].isna().sum())
        if dropped:
            print(f"WARNING: dropped {dropped} bars with invalid local time (DST transition).")
        df = (raw.dropna(subset=["time_utc"])[COLUMNS]
              .drop_duplicates("time_utc")
              .sort_values("time_utc")
              .reset_index(drop=True)
              .astype({"open": "float64", "high": "float64", "low": "float64", "close": "float64",
                       "tick_volume": "int64", "spread": "int32"}))

        report(df, start)

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)

        meta = {
            "symbol": args.symbol,
            "timeframe": "M1",
            "server_tz_mode": args.server_tz,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": len(df),
            "first_utc": str(df["time_utc"].iloc[0]),
            "last_utc": str(df["time_utc"].iloc[-1]),
            "digits": info.digits,
            "point": info.point,
            "trade_contract_size": info.trade_contract_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_tick_size": info.trade_tick_size,
            "volume_min": info.volume_min,
            "volume_step": info.volume_step,
            "volume_max": info.volume_max,
            "trade_stops_level": info.trade_stops_level,
            "currency_profit": info.currency_profit,
        }
        meta_path = out.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        size_mb = out.stat().st_size / 1_048_576
        print(f"\nSaved {out} ({size_mb:.1f} MB) and {meta_path}")
        if size_mb > 90:
            print("WARNING: file is close to GitHub's 100 MB limit; export fewer years.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
