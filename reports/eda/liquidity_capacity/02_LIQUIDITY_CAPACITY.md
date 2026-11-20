# Liquidity Capacity Diagnostic

## Research question

Does the historical universe contain at least 50 securities with positive trailing 60-session VALUE on each market date?

## Methodology

- Source: `data\market_data\moex_daily_adjusted.sqlite`
- Table: `adjusted_daily_bars`
- Analysis start: `2005-01-01`
- Boards included: `TQBR, EQBR`
- Excluded securities: `GAZC, GAZS, GAZT`
- VALUE is measured in RUB.
- Board-level VALUE is summed for each SECID/date.
- Missing observations are zero only inside observed ticker lifetime.
- No forward fill is applied.
- Rolling VALUE uses 60 market sessions, including the current session.
- A ticker is eligible only if its full rolling window exists and VALUE is positive.
- The 2022-02-28 through 2022-03-23 pause is excluded.

## Results

- Market dates: 5,479
- Tickers: 379
- Dates with at least 50 eligible tickers: 3,071
- Dates below TOP-50 capacity: 2,408
- Share of dates with TOP-50 capacity: 56.05%
- Minimum eligible count: 0 on 2005-01-11
- Maximum eligible count: 230 on 2014-09-02

## Board aggregation audit

- Raw rows after filters: 691,241
- SECID/date groups before aggregation: 689,919
- Multi-board SECID/date groups: 1,322

## Shortage periods

| Start | End | Sessions | Min eligible | Max eligible |
|---|---|---:|---:|---:|
| 2005-01-11 | 2014-09-01 | 2408 | 0 | 42 |

## Interpretation limits

- This is a historical universe-capacity diagnostic, not proof of executable capacity.
- VALUE is a turnover proxy. It does not measure order-book depth, spread, market impact, or participation limits.
- Summing approved boards estimates turnover across the selected board set. A strategy restricted to one board requires a separate test.
- No inflation adjustment is applied because ranking is cross-sectional within each date.

## Output files

- `daily_capacity.csv`
- `shortage_periods.csv`
- `summary.csv`
