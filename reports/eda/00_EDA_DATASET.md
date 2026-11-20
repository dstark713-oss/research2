# EDA Dataset

## Observation unit

One row represents one ticker on one market session.
The panel contains only ticker/date observations selected
by the point-in-time historical liquidity universe.

## Source

- Database: `C:\Users\Danil\Desktop\research2\data\market_data\moex_daily_adjusted.sqlite`
- Membership: `C:\Users\Danil\Desktop\research2\data\derived\historical_universe\universe_membership.parquet`
- Analysis start: `2005-01-01`
- Pipeline version: `1.0.0`

## Panel dimensions

- Rows: 197,688
- Tickers: 207
- Dates: 5,420
- First date: 2005-04-06
- Last date: 2026-11-16

## Features

- `tradedate`
- `secid`
- `value_60d`
- `rank`
- `universe_size`
- `selection_method`
- `volume`
- `value`
- `n_boards`
- `adjusted_open`
- `adjusted_high`
- `adjusted_low`
- `adjusted_close`
- `price_board`
- `bar_available`
- `return_1d`
- `abs_return`
- `squared_return`
- `high_low_range`
- `open_close_body`
- `overnight_gap`
- `log_volume`
- `log_value`
- `rv_5`
- `rv_20`
- `mean_abs_return_5`
- `mean_abs_return_20`
- `value_sum_20`
- `value_mean_20`
- `volume_sum_20`
- `volume_mean_20`
- `value_sum_60`
- `value_mean_60`
- `volume_sum_60`
- `volume_mean_60`
- `value_to_median_20`
- `zero_value_share_20`
- `zero_volume_share_20`
- `feature_source_available`

## Missing values

| Column | Missing share |
|---|---:|
| `mean_abs_return_20` | 3.5708% |
| `rv_20` | 3.5708% |
| `mean_abs_return_5` | 1.8195% |
| `rv_5` | 1.8195% |
| `abs_return` | 1.1129% |
| `squared_return` | 1.1129% |
| `overnight_gap` | 1.1129% |
| `return_1d` | 1.1129% |
| `high_low_range` | 1.1124% |
| `open_close_body` | 0.8286% |
| `adjusted_open` | 0.8286% |
| `adjusted_close` | 0.8286% |
| `adjusted_high` | 0.8281% |
| `adjusted_low` | 0.8281% |
| `value_to_median_20` | 0.2747% |
| `volume` | 0.0000% |
| `selection_method` | 0.0000% |
| `universe_size` | 0.0000% |
| `rank` | 0.0000% |
| `value_60d` | 0.0000% |
| `secid` | 0.0000% |
| `tradedate` | 0.0000% |
| `n_boards` | 0.0000% |
| `log_value` | 0.0000% |
| `log_volume` | 0.0000% |
| `price_board` | 0.0000% |
| `bar_available` | 0.0000% |
| `value` | 0.0000% |
| `value_sum_20` | 0.0000% |
| `volume_sum_20` | 0.0000% |
| `value_mean_20` | 0.0000% |
| `volume_mean_20` | 0.0000% |
| `value_sum_60` | 0.0000% |
| `volume_sum_60` | 0.0000% |
| `value_mean_60` | 0.0000% |
| `volume_mean_60` | 0.0000% |
| `zero_value_share_20` | 0.0000% |
| `zero_volume_share_20` | 0.0000% |
| `feature_source_available` | 0.0000% |

## Smoke tests

- PASS: `non_empty`
- PASS: `unique_date_ticker`
- PASS: `membership_row_count_preserved`
- PASS: `no_excluded_tickers`
- PASS: `universe_size_limit`
- PASS: `date_minimum`
- PASS: `required_features_present`
- PASS: `no_infinite_numeric_values`

## Construction notes

- Features are calculated using the full available history
  of each ticker before filtering to daily universe membership.
- No forward fill is applied to prices.
- Missing sessions are not bridged when calculating daily returns.
- VALUE and volume are summed across approved boards.
- OHLC is sourced from TQBR where available, otherwise EQBR.
- Rows are retained only when the ticker belongs to the
  historical universe on that date.
- Rolling features are backward-looking.

## Limitations

- This is a feature dataset, not a finalized ML matrix.
- Target labels are not calculated in this stage.
- Current-date membership is known after the close.
  For next-session decisions, shift membership by one session.
- Missing feature values are preserved for later analysis.
