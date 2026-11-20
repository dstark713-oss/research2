# Block 02. Temporal Dependence

## Research question

Does the past of each ticker contain measurable temporal dependence in returns, volatility proxies, volume and money turnover?

## Dataset

- Rows: 197,688
- Tickers: 207
- First date: 2005-04-06
- Last date: 2026-11-16
- Minimum observations per ticker-series: 250
- Maximum ACF lag: 40

## Methodology

- ACF is calculated independently for each ticker.
- Cross-sectional summaries describe the distribution across tickers.
- Ljung-Box tests are calculated per ticker and feature.
- P-values are descriptive diagnostics; multiple testing is not corrected in this block.
- Hurst R/S is a rough diagnostic and is not treated as proof of long memory.
- Missing observations are not forward-filled.
- No observations are winsorized or automatically removed.

## ACF summary

The table reports cross-sectional summaries of ticker-level ACF.

| Feature | Metric | Tickers | Median | Q25 | Q75 | Positive share |
|---|---:|---:|---:|---:|---:|---:|
| abs_return | acf_lag_1 | 111 | 0.2458 | 0.2056 | 0.3019 | 1.000 |
| abs_return | acf_lag_5 | 111 | 0.1360 | 0.0935 | 0.1787 | 0.982 |
| abs_return | acf_lag_20 | 111 | 0.0787 | 0.0387 | 0.1037 | 0.919 |
| log_value | acf_lag_1 | 111 | 0.6643 | 0.5346 | 0.8037 | 1.000 |
| log_value | acf_lag_5 | 111 | 0.4607 | 0.3214 | 0.6650 | 0.982 |
| log_value | acf_lag_20 | 111 | 0.2982 | 0.1571 | 0.5096 | 0.991 |
| log_volume | acf_lag_1 | 111 | 0.6819 | 0.5546 | 0.8023 | 1.000 |
| log_volume | acf_lag_5 | 111 | 0.4732 | 0.3263 | 0.6721 | 0.982 |
| log_volume | acf_lag_20 | 111 | 0.3387 | 0.1405 | 0.4930 | 0.964 |
| return_1d | acf_lag_1 | 111 | 0.0371 | 0.0066 | 0.0804 | 0.784 |
| return_1d | acf_lag_5 | 111 | 0.0048 | -0.0184 | 0.0317 | 0.586 |
| return_1d | acf_lag_20 | 111 | 0.0020 | -0.0195 | 0.0244 | 0.541 |
| squared_return | acf_lag_1 | 111 | 0.1659 | 0.1109 | 0.2569 | 0.991 |
| squared_return | acf_lag_5 | 111 | 0.0690 | 0.0230 | 0.1125 | 0.919 |
| squared_return | acf_lag_20 | 111 | 0.0255 | 0.0006 | 0.0505 | 0.757 |

## Ljung-Box summary

Share of ticker-level tests with p-value below 0.05. These are unadjusted p-values.

| Feature | Lag | Tickers | Significant share | Median p-value |
|---|---:|---:|---:|---:|
| abs_return | 5 | 111 | 0.991 | 4.43139e-62 |
| abs_return | 10 | 111 | 0.991 | 4.7263e-81 |
| abs_return | 20 | 111 | 0.991 | 1.93729e-96 |
| abs_return | 40 | 111 | 0.973 | 9.45845e-122 |
| log_value | 5 | 111 | 1.000 | 0 |
| log_value | 10 | 111 | 1.000 | 0 |
| log_value | 20 | 111 | 1.000 | 0 |
| log_value | 40 | 111 | 1.000 | 0 |
| log_volume | 5 | 111 | 1.000 | 0 |
| log_volume | 10 | 111 | 1.000 | 0 |
| log_volume | 20 | 111 | 1.000 | 0 |
| log_volume | 40 | 111 | 1.000 | 0 |
| return_1d | 5 | 111 | 0.622 | 0.0131761 |
| return_1d | 10 | 111 | 0.649 | 0.00970367 |
| return_1d | 20 | 111 | 0.586 | 0.0168283 |
| return_1d | 40 | 111 | 0.586 | 0.0206441 |
| squared_return | 5 | 111 | 0.901 | 2.66792e-20 |
| squared_return | 10 | 111 | 0.883 | 1.85753e-22 |
| squared_return | 20 | 111 | 0.865 | 2.99119e-26 |
| squared_return | 40 | 111 | 0.838 | 1.30858e-27 |

## ACF decay diagnostics

Kendall and Spearman coefficients describe the relationship between lag and positive ACF values. They are descriptive only.

- Ticker-series observations: 555
- Median Kendall tau: -0.5051
- Median Spearman rho: -0.6585

## Hurst R/S diagnostics

- **abs_return:** n=87, median=0.7446, Q25=0.7255, Q75=0.7700
- **return_1d:** n=87, median=0.5964, Q25=0.5756, Q75=0.6166
- **squared_return:** n=87, median=0.6886, Q25=0.6695, Q75=0.7119

## Figures

![acf_abs_return](02_temporal_dependence\figures\acf_abs_return.png)

![acf_log_value](02_temporal_dependence\figures\acf_log_value.png)

![acf_log_volume](02_temporal_dependence\figures\acf_log_volume.png)

![acf_return_1d](02_temporal_dependence\figures\acf_return_1d.png)

![acf_squared_return](02_temporal_dependence\figures\acf_squared_return.png)

![distribution_acf_lag_1](02_temporal_dependence\figures\distribution_acf_lag_1.png)

![distribution_acf_lag_5](02_temporal_dependence\figures\distribution_acf_lag_5.png)

![distribution_acf_lag_20](02_temporal_dependence\figures\distribution_acf_lag_20.png)

![ljung_box_significance_lag_5](02_temporal_dependence\figures\ljung_box_significance_lag_5.png)

![ljung_box_significance_lag_10](02_temporal_dependence\figures\ljung_box_significance_lag_10.png)

![ljung_box_significance_lag_20](02_temporal_dependence\figures\ljung_box_significance_lag_20.png)

![ljung_box_significance_lag_40](02_temporal_dependence\figures\ljung_box_significance_lag_40.png)

![hurst_rs_distribution](02_temporal_dependence\figures\hurst_rs_distribution.png)

## Limitations

- ACF is calculated on the sequence of available non-null observations. If a ticker has missing market sessions, the lag index is an observation lag rather than necessarily a consecutive market-session lag.
- Ljung-Box p-values are not corrected for multiple testing.
- Cross-sectional medians can hide substantial ticker heterogeneity.
- R/S Hurst estimates are sensitive to short samples, structural breaks and non-stationarity.
- This block does not establish predictive usefulness or causal effects.

## Outputs

- `tables/ticker_acf.csv`
- `tables/acf_by_lag.csv`
- `tables/acf_summary.csv`
- `tables/ljung_box.csv`
- `tables/ljung_box_summary.csv`
- `tables/acf_decay.csv`
- `tables/hurst_rs.csv`

## Status

Descriptive temporal-dependence analysis only. No model or strategy conclusions are drawn in this report.