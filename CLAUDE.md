# XAUUSD SMC Pullback Scalper

Python scalping bot for XAUUSD on MetaTrader 5, built in phases from a written spec.

## Source of truth

- `docs/strategy-plan.md` is the spec. Section numbers below refer to it.
- Never change strategy rules, default parameters, cost assumptions or acceptance criteria on your own. If the spec is ambiguous or looks wrong, stop and ask, and list the options.
- Implement only the phase you are asked for (section 12). End every phase with: test results, spec ambiguities you hit, assumptions you made, and what the next phase needs.

## Environment constraints

- Cloud sessions run on Linux. The `MetaTrader5` package is Windows-only and cannot be installed here.
- Import `MetaTrader5` only inside `src/scalper/mt5_worker.py`, lazily, inside functions. Every other module must import and pass tests on Linux.
- Tests use `tests/fakes/fake_mt5.py` (a stub with the same call signatures) instead of the real terminal.
- `requirements.txt` = runtime deps for Windows (includes MetaTrader5). `requirements-dev.txt` = everything except MetaTrader5, plus pytest, ruff, pyarrow.
- Python 3.11. Type hints everywhere. `ruff check` clean.

## Data

- `data/xauusd_m1.parquet`: M1 bars, columns `time_utc, open, high, low, close, tick_volume, spread` (spread in points). Bid-based.
- `data/xauusd_m1.meta.json`: symbol spec (digits, point, tick value/size, volume min/step/max). Use it for sizing in backtests.
- No TradingView export is available (the user's TradingView plan cannot export chart data). This supersedes the TradingView-export parity item in section 12. Parity is tested instead against `tests/reference_pine.py`: plain, loop-based transcriptions of the Pine Script v6 reference implementations of `ta.sma`, `ta.ema` (SMA-seeded), `ta.rma` (SMA-seeded), `ta.tr` (first bar = high − low), `ta.atr` and `ta.pivothigh` / `ta.pivotlow`, written for readability, not speed. Production indicators (streaming and vectorized) must match them within 1e-9 on the development slice of the parquet. Document the pivot tie-breaking rule you implement and cite the Pine reference behaviour it follows; if unsure, stop and ask.
- For a visual sanity check, provide `python -m tools.dump_structure --date YYYY-MM-DD` that prints every confirmed pivot and BOS/CHoCH event for one UTC day, so the user can compare it with the indicator's labels on TradingView (feeds differ slightly, so compare positions, not exact prices).
- Broker facts from the export (`XAUUSD.vx`): digits 2, point 0.01, contract 100 oz, tick value 1.0 USD per 0.01 per lot, volume 0.01–50 step 0.01, stops level 31 points. Recorded spread is floored at 28 points (median 28, p75 29, max 273); 25.5% of bars record spread 0, which means missing, not free.
- Backtest cost defaults in `config.yaml`: `symbol: XAUUSD.vx`, `min_spread_points: 28` (replaces 0 and anything below it), `max_spread: 0.35` (price units, live filter), `slippage: 0.05`, `commission_per_lot: 0.0` until the user confirms the broker's commission.
- **Locked out-of-sample data**: the last 30% of `xauusd_m1.parquet` by time (section 11) must never be loaded during development, tuning or tests. Only a run explicitly requested as "locked OOS run" may touch it. Enforce this in the data loader with a guard.

## Non-negotiable rules (section 12)

- No look-ahead: closed bars only; pivots used only after confirmation (`p + L`); entries fill on the next bar.
- `strategy.py`, `indicators.py`, `structure.py`, `risk.py`, `trade_manager.py` are pure: no MT5 import, no system clock, deterministic.
- The backtester runs the exact same `strategy` and `trade_manager` code as live.
- All times UTC internally. All parameters come from `config.yaml` (validated with pydantic). No magic numbers.
- Every evaluated signal, including rejected ones, is journaled with the name of the failing condition.

## Layout

```
src/scalper/        config.py mt5_worker.py indicators.py structure.py strategy.py
                    risk.py trade_manager.py state_store.py journal.py ui.py main.py
backtest/           sim_broker.py engine.py report.py validation.py
tests/              unit tests + fakes/
docs/               strategy-plan.md
reports/<phase>/    committed backtest outputs
```

## Commands

```
pip install -r requirements-dev.txt
pytest -q
ruff check .
python -m backtest.engine --config config.yaml --data data/xauusd_m1.parquet --out reports/<name>
```
