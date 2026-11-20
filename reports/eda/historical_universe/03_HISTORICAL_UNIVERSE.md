# Historical Universe

## Configuration

- Analysis start: `2005-01-01`
- Selection: TOP-50 by trailing 60-session VALUE
- Boards: `TQBR, EQBR`
- Excluded tickers: `GAZC, GAZS, GAZT`
- Excluded suspension: `2022-02-28` to `2022-03-23`
- Pipeline version: `1.0.0`

## Construction rules

1. VALUE is summed across approved boards for each ticker/date.
2. OHLC is taken from TQBR when available, otherwise EQBR.
3. Missing turnover inside the observed ticker lifetime is treated as zero.
4. Dates before a ticker's first observation are not treated as zero.
5. A ticker needs a complete 60-session history to qualify.
6. Ranking uses only trailing VALUE available on the selection date.
7. If fewer than 50 tickers qualify, all eligible tickers are selected.
8. No forward fill is applied to OHLC.
9. Missing current-day bars remain explicit in the panel.

## Results

- Market dates: 5,479
- Membership rows: 197,688
- Panel rows: 197,688
- Panel tickers: 207
- Panel date range: 2005-04-06 to 2026-11-16

### Selection methods

{
  "TOP50_VALUE_60D": 3071,
  "ALL_AVAILABLE": 2349,
  "NO_ELIGIBLE_TICKERS": 59
}

## Output files

- `data/derived/historical_universe/universe_membership.parquet`
- `data/derived/historical_universe/universe_panel.parquet`
- `data/derived/historical_universe/metadata.json`
- `reports/eda/historical_universe/summary.csv`
- `reports/eda/historical_universe/daily_universe_summary.csv`

## Important limitations

- Trailing VALUE is a historical turnover ranking, not a direct measure of order-book depth,
  spread, market impact, or executable capacity.
- The current-day universe is known only after that day's closing data is available.
  For next-session trading, use the membership calculated at the previous close.
- OHLC is sourced from one preferred board, while VALUE and volume are summed across
  approved boards. This is a deliberate aggregation convention and should remain documented.
- The panel is a filtered research dataset, not a claim that every selected ticker
  was tradable at every point during every session.
