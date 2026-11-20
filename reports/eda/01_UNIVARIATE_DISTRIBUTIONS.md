# 01. Univariate Distributions

## Research question

What are the empirical distributional properties of each variable,
and how strongly do these properties differ across tickers?

## Observation unit

One row represents one ticker on one market session.
Observations are not assumed to be independent.

## Dataset

- Rows: 197,688
- Tickers: 207
- Dates: 5,420
- First date: 2005-04-06
- Last date: 2026-11-16

## Methodology

- Minimum observations per ticker: 60
- Pooled statistics are calculated over all available panel rows.
- Ticker-level statistics are calculated independently per ticker.
- Missing and infinite values are excluded per variable.
- No winsorization or outlier deletion is applied.
- Excess kurtosis uses Fisher definition (normal = 0).
- Robust sigma = 1.4826 × median absolute deviation.
- Histogram samples are capped for rendering only.

## Pooled distribution statistics

Full table: `tables/pooled_statistics.csv`

## Across-ticker statistics

Full table: `tables/ticker_statistics_summary.csv`

Ticker-level records: `tables/ticker_statistics.csv`

## Extreme observations

Extreme observations use the 99.9th percentile of absolute values
for each feature. They are flagged for inspection, not classified
as errors.

Full table: `tables/extreme_observations.csv`

## Distribution plots

### return_1d_hist

![return_1d_hist](01_univariate_distributions/figures/return_1d_hist.png)

### abs_return_hist

![abs_return_hist](01_univariate_distributions/figures/abs_return_hist.png)

### abs_return_log1p_hist

![abs_return_log1p_hist](01_univariate_distributions/figures/abs_return_log1p_hist.png)

### squared_return_hist

![squared_return_hist](01_univariate_distributions/figures/squared_return_hist.png)

### squared_return_log1p_hist

![squared_return_log1p_hist](01_univariate_distributions/figures/squared_return_log1p_hist.png)

### high_low_range_hist

![high_low_range_hist](01_univariate_distributions/figures/high_low_range_hist.png)

### open_close_body_hist

![open_close_body_hist](01_univariate_distributions/figures/open_close_body_hist.png)

### overnight_gap_hist

![overnight_gap_hist](01_univariate_distributions/figures/overnight_gap_hist.png)

### volume_hist

![volume_hist](01_univariate_distributions/figures/volume_hist.png)

### volume_log1p_hist

![volume_log1p_hist](01_univariate_distributions/figures/volume_log1p_hist.png)

### value_hist

![value_hist](01_univariate_distributions/figures/value_hist.png)

### value_log1p_hist

![value_log1p_hist](01_univariate_distributions/figures/value_log1p_hist.png)

### log_volume_hist

![log_volume_hist](01_univariate_distributions/figures/log_volume_hist.png)

### log_volume_log1p_hist

![log_volume_log1p_hist](01_univariate_distributions/figures/log_volume_log1p_hist.png)

### log_value_hist

![log_value_hist](01_univariate_distributions/figures/log_value_hist.png)

### log_value_log1p_hist

![log_value_log1p_hist](01_univariate_distributions/figures/log_value_log1p_hist.png)

### rv_5_hist

![rv_5_hist](01_univariate_distributions/figures/rv_5_hist.png)

### rv_5_log1p_hist

![rv_5_log1p_hist](01_univariate_distributions/figures/rv_5_log1p_hist.png)

### rv_20_hist

![rv_20_hist](01_univariate_distributions/figures/rv_20_hist.png)

### rv_20_log1p_hist

![rv_20_log1p_hist](01_univariate_distributions/figures/rv_20_log1p_hist.png)

### mean_abs_return_5_hist

![mean_abs_return_5_hist](01_univariate_distributions/figures/mean_abs_return_5_hist.png)

### mean_abs_return_5_log1p_hist

![mean_abs_return_5_log1p_hist](01_univariate_distributions/figures/mean_abs_return_5_log1p_hist.png)

### mean_abs_return_20_hist

![mean_abs_return_20_hist](01_univariate_distributions/figures/mean_abs_return_20_hist.png)

### mean_abs_return_20_log1p_hist

![mean_abs_return_20_log1p_hist](01_univariate_distributions/figures/mean_abs_return_20_log1p_hist.png)

### value_sum_20_hist

![value_sum_20_hist](01_univariate_distributions/figures/value_sum_20_hist.png)

### value_sum_20_log1p_hist

![value_sum_20_log1p_hist](01_univariate_distributions/figures/value_sum_20_log1p_hist.png)

### value_sum_60_hist

![value_sum_60_hist](01_univariate_distributions/figures/value_sum_60_hist.png)

### value_sum_60_log1p_hist

![value_sum_60_log1p_hist](01_univariate_distributions/figures/value_sum_60_log1p_hist.png)

### value_mean_20_hist

![value_mean_20_hist](01_univariate_distributions/figures/value_mean_20_hist.png)

### value_mean_20_log1p_hist

![value_mean_20_log1p_hist](01_univariate_distributions/figures/value_mean_20_log1p_hist.png)

### value_mean_60_hist

![value_mean_60_hist](01_univariate_distributions/figures/value_mean_60_hist.png)

### value_mean_60_log1p_hist

![value_mean_60_log1p_hist](01_univariate_distributions/figures/value_mean_60_log1p_hist.png)

### volume_sum_20_hist

![volume_sum_20_hist](01_univariate_distributions/figures/volume_sum_20_hist.png)

### volume_sum_20_log1p_hist

![volume_sum_20_log1p_hist](01_univariate_distributions/figures/volume_sum_20_log1p_hist.png)

### volume_sum_60_hist

![volume_sum_60_hist](01_univariate_distributions/figures/volume_sum_60_hist.png)

### volume_sum_60_log1p_hist

![volume_sum_60_log1p_hist](01_univariate_distributions/figures/volume_sum_60_log1p_hist.png)

### value_to_median_20_hist

![value_to_median_20_hist](01_univariate_distributions/figures/value_to_median_20_hist.png)

### zero_value_share_20_hist

![zero_value_share_20_hist](01_univariate_distributions/figures/zero_value_share_20_hist.png)

### zero_volume_share_20_hist

![zero_volume_share_20_hist](01_univariate_distributions/figures/zero_volume_share_20_hist.png)

### ticker_return_1d_std_boxplot

![ticker_return_1d_std_boxplot](01_univariate_distributions/figures/ticker_return_1d_std_boxplot.png)

### ticker_abs_return_std_boxplot

![ticker_abs_return_std_boxplot](01_univariate_distributions/figures/ticker_abs_return_std_boxplot.png)

### ticker_rv_5_std_boxplot

![ticker_rv_5_std_boxplot](01_univariate_distributions/figures/ticker_rv_5_std_boxplot.png)

### ticker_rv_20_std_boxplot

![ticker_rv_20_std_boxplot](01_univariate_distributions/figures/ticker_rv_20_std_boxplot.png)

### ticker_volume_median_boxplot

![ticker_volume_median_boxplot](01_univariate_distributions/figures/ticker_volume_median_boxplot.png)

### ticker_value_median_boxplot

![ticker_value_median_boxplot](01_univariate_distributions/figures/ticker_value_median_boxplot.png)

### ticker_log_volume_median_boxplot

![ticker_log_volume_median_boxplot](01_univariate_distributions/figures/ticker_log_volume_median_boxplot.png)

### ticker_log_value_median_boxplot

![ticker_log_value_median_boxplot](01_univariate_distributions/figures/ticker_log_value_median_boxplot.png)

## Output files

- `tables/pooled_statistics.csv`
- `tables/ticker_statistics.csv`
- `tables/ticker_statistics_summary.csv`
- `tables/extreme_observations.csv`
- `figures/*.png`

## Limitations

- Pooled distributions mix different tickers and time periods.
- Cross-sectional differences may reflect different history lengths.
- Extreme observations are not automatically data errors.
- Distributional summaries do not establish predictive power.
- This block does not evaluate future volatility targets.
