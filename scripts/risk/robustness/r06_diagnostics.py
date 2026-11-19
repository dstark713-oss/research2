
# Path: scripts/risk/robustness/r06_diagnostics.py
# Description: Final R06 diagnostics: forward risk prediction, rolling performance and serial-preserving placebo tests.

import argparse
import sys

from pathlib import Path

import numpy as np
import pandas as pd


RISK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RISK_DIR))

from config import DB_PATH, COST_BPS
from data import load_prices

from metrics import (
    simulate_daily,
    equal_weight_targets,
    buy_and_hold_targets,
)

from strategies import r06_volatility_weights


TRADING_DAYS = 252

VOL_WINDOW = 20
REFERENCE_WINDOW = 252
THRESHOLD_MEDIUM = 1.50
THRESHOLD_HIGH = 2.00

FORWARD_HORIZONS = [5, 20, 60]
ROLLING_WINDOWS = [252, 504]

N_CIRCULAR = 300
N_BLOCK = 200
BLOCK_LENGTH = 60
RANDOM_SEED = 20261119


def parse_args():
    parser = argparse.ArgumentParser(
        description="Final R06 diagnostic tests."
    )

    parser.add_argument("--db", type=Path, default=DB_PATH)

    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parents[3]
            / "data"
            / "reports"
            / "risk_control"
            / "r06_diagnostics"
        ),
    )

    parser.add_argument(
        "--cost-bps",
        type=float,
        default=COST_BPS,
    )

    return parser.parse_args()


def calculate_signal(prices):
    returns = (
        prices.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    basket_returns = returns.mean(axis=1)

    short_vol = (
        basket_returns
        .rolling(
            VOL_WINDOW,
            min_periods=max(5, VOL_WINDOW // 2),
        )
        .std()
        * np.sqrt(TRADING_DAYS)
    )

    reference_vol = short_vol.rolling(
        REFERENCE_WINDOW,
        min_periods=60,
    ).median()

    ratio = (
        short_vol
        / reference_vol.replace(0.0, np.nan)
    )

    exposure = pd.Series(
        1.0,
        index=prices.index,
        dtype=float,
    )

    exposure.loc[ratio > THRESHOLD_MEDIUM] = 0.50
    exposure.loc[ratio > THRESHOLD_HIGH] = 0.25

    signal = pd.DataFrame({
        "basket_return": basket_returns,
        "short_volatility": short_vol,
        "reference_volatility": reference_vol,
        "volatility_ratio": ratio,
        "target_exposure": exposure,
    })

    return returns, signal


def forward_matrix(series, horizon):
    return pd.concat(
        {
            step: series.shift(-step)
            for step in range(1, horizon + 1)
        },
        axis=1,
    )


def forward_risk_tests(basket_returns, signal):
    rows = []

    ratio = signal["volatility_ratio"]

    regime = pd.Series(
        pd.NA,
        index=ratio.index,
        dtype="object",
    )

    valid_ratio = ratio.notna()

    regime.loc[valid_ratio] = "LOW"
    regime.loc[ratio > THRESHOLD_MEDIUM] = "MEDIUM"
    regime.loc[ratio > THRESHOLD_HIGH] = "HIGH"

    for horizon in FORWARD_HORIZONS:
        future = forward_matrix(
            basket_returns,
            horizon,
        )

        future_vol = (
            future.std(axis=1)
            * np.sqrt(TRADING_DAYS)
        )

        future_wealth = (1.0 + future).cumprod(axis=1)

        wealth_with_start = np.column_stack([
            np.ones(len(future_wealth)),
            future_wealth.to_numpy(),
        ])

        peaks = np.maximum.accumulate(
            wealth_with_start,
            axis=1,
        )[:, 1:]

        future_drawdown = pd.Series(
            (
                future_wealth.to_numpy() / peaks - 1.0
            ).min(axis=1),
            index=future.index,
        )

        future_return = future_wealth.iloc[:, -1] - 1.0

        sample = pd.DataFrame({
            "ratio": ratio,
            "regime": regime,
            "future_volatility": future_vol,
            "future_drawdown": future_drawdown,
            "future_return": future_return,
        }).dropna()

        rows.append({
            "horizon": horizon,
            "observations": len(sample),
            "spearman_ratio_future_vol": (
                sample["ratio"].corr(
                    sample["future_volatility"],
                    method="spearman",
                )
            ),
            "spearman_ratio_future_drawdown": (
                sample["ratio"].corr(
                    sample["future_drawdown"],
                    method="spearman",
                )
            ),
            "spearman_ratio_future_return": (
                sample["ratio"].corr(
                    sample["future_return"],
                    method="spearman",
                )
            ),
        })

        for state in ["LOW", "MEDIUM", "HIGH"]:
            group = sample.loc[sample["regime"] == state]

            rows.append({
                "horizon": horizon,
                "regime": state,
                "observations": len(group),
                "mean_future_volatility": (
                    group["future_volatility"].mean()
                ),
                "median_future_volatility": (
                    group["future_volatility"].median()
                ),
                "mean_future_drawdown": (
                    group["future_drawdown"].mean()
                ),
                "mean_future_return": (
                    group["future_return"].mean()
                ),
            })

    return pd.DataFrame(rows)


def rolling_performance(results):
    rows = []

    for name, result in results.items():
        returns = result["returns"]

        for window in ROLLING_WINDOWS:
            rolling_mean = returns.rolling(
                window,
                min_periods=window,
            ).mean()

            rolling_std = returns.rolling(
                window,
                min_periods=window,
            ).std()

            sharpe = (
                rolling_mean
                / rolling_std.replace(0.0, np.nan)
                * np.sqrt(TRADING_DAYS)
            )

            for date, value in sharpe.dropna().items():
                rows.append({
                    "date": date,
                    "strategy": name,
                    "window": window,
                    "rolling_sharpe": value,
                })

    return pd.DataFrame(rows)


def run_placebo(
    prices,
    actual_exposure,
    cost_bps,
    rng,
):
    n = len(actual_exposure)
    values = actual_exposure.to_numpy(dtype=float)

    records = []

    def evaluate(name, exposure, iteration):
        # Keep initial deployment identical across all tests.
        exposure = exposure.copy()
        exposure[0] = values[0]

        targets = equal_weight_targets(
            prices.index,
            prices.columns,
            exposure=1.0,
        )

        targets = targets.mul(
            pd.Series(exposure, index=prices.index),
            axis=0,
        )

        result = simulate_daily(
            prices,
            targets,
            cost_bps=cost_bps,
        )

        records.append({
            "placebo_type": name,
            "iteration": iteration,
            "Sharpe": result["metrics"]["Sharpe"],
            "CAGR": result["metrics"]["CAGR"],
            "MDD": result["metrics"]["MDD"],
            "average_exposure": result["metrics"]["average_exposure"],
            "annualized_turnover": (
                result["metrics"]["annualized_turnover"]
            ),
        })

    # Actual sequence.
    evaluate("ACTUAL", values, 0)

    # Circular shifts preserve the complete sequence and its
    # serial structure, but break its original market alignment.
    for i in range(N_CIRCULAR):
        shift = int(rng.integers(20, n - 20))
        shifted = np.roll(values, shift)

        evaluate(
            "CIRCULAR_SHIFT",
            shifted,
            i + 1,
        )

    # Block permutation preserves local sequences within
    # 60-observation blocks, but changes their order.
    blocks = [
        values[start:start + BLOCK_LENGTH]
        for start in range(0, n, BLOCK_LENGTH)
    ]

    for i in range(N_BLOCK):
        order = rng.permutation(len(blocks))

        permuted = np.concatenate([
            blocks[j] for j in order
        ])[:n]

        evaluate(
            "BLOCK_PERMUTATION",
            permuted,
            i + 1,
        )

    return pd.DataFrame(records)


def placebo_summary(placebo):
    actual = placebo.loc[
        placebo["placebo_type"] == "ACTUAL"
    ].iloc[0]

    rows = []

    for name in ["CIRCULAR_SHIFT", "BLOCK_PERMUTATION"]:
        null = placebo.loc[
            placebo["placebo_type"] == name
        ]

        for metric in ["Sharpe", "CAGR", "MDD"]:
            observed = actual[metric]
            distribution = null[metric]

            p_value = (
                1 + (distribution >= observed).sum()
            ) / (len(distribution) + 1)

            rows.append({
                "placebo_type": name,
                "metric": metric,
                "actual": observed,
                "null_median": distribution.median(),
                "null_5pct": distribution.quantile(0.05),
                "null_95pct": distribution.quantile(0.95),
                "empirical_p_better": p_value,
            })

    return pd.DataFrame(rows)


def main():
    args = parse_args()

    if args.cost_bps < 0:
        raise ValueError("Cost cannot be negative.")

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    prices = load_prices(args.db)

    if len(prices) < 750:
        raise RuntimeError(
            "Insufficient history for R06 diagnostics."
        )

    returns, signal = calculate_signal(prices)

    # Independent implementation check against the strategy.
    strategy_weights = r06_volatility_weights(
        prices,
        returns,
        vol_window=VOL_WINDOW,
        reference_window=REFERENCE_WINDOW,
        threshold_medium=THRESHOLD_MEDIUM,
        threshold_high=THRESHOLD_HIGH,
    )

    strategy_exposure = strategy_weights.sum(axis=1)

    if not np.allclose(
        strategy_exposure.to_numpy(),
        signal["target_exposure"].to_numpy(),
    ):
        raise RuntimeError(
            "R06 signal reconstruction does not match strategy."
        )

    # Main comparison.
    targets = {
        "R06": strategy_weights,
        "EQUAL_WEIGHT_DAILY": equal_weight_targets(
            prices.index,
            prices.columns,
        ),
        "BUY_AND_HOLD": buy_and_hold_targets(
            prices.index,
            prices.columns,
        ),
    }

    r06_result = simulate_daily(
        prices,
        strategy_weights,
        cost_bps=args.cost_bps,
    )

    matched_exposure = (
        r06_result["metrics"]["average_exposure"]
    )

    targets["MATCHED_EXPOSURE"] = equal_weight_targets(
        prices.index,
        prices.columns,
        exposure=matched_exposure,
    )

    results = {
        name: simulate_daily(
            prices,
            weights,
            cost_bps=args.cost_bps,
        )
        for name, weights in targets.items()
    }

    summary = pd.DataFrame([
        {
            "strategy": name,
            **result["metrics"],
        }
        for name, result in results.items()
    ])

    # Forward risk prediction.
    forward = forward_risk_tests(
        signal["basket_return"],
        signal,
    )

    # Rolling Sharpe.
    rolling = rolling_performance(results)

    # Serial-preserving placebo tests.
    rng = np.random.default_rng(RANDOM_SEED)

    placebo = run_placebo(
        prices,
        strategy_exposure,
        args.cost_bps,
        rng,
    )

    placebo_stats = placebo_summary(placebo)

    # Save outputs.
    signal.to_csv(
        args.output / "signal_series.csv",
        index_label="tradedate",
    )

    forward.to_csv(
        args.output / "forward_risk_tests.csv",
        index=False,
    )

    summary.to_csv(
        args.output / "strategy_summary.csv",
        index=False,
    )

    rolling.to_csv(
        args.output / "rolling_performance.csv",
        index=False,
    )

    placebo.to_csv(
        args.output / "placebo_distribution.csv",
        index=False,
    )

    placebo_stats.to_csv(
        args.output / "placebo_summary.csv",
        index=False,
    )

    print("\nSTRATEGY SUMMARY")
    print(summary.to_string(index=False))

    print("\nFORWARD RISK TESTS")
    print(forward.to_string(index=False))

    print("\nPLACEBO SUMMARY")
    print(placebo_stats.to_string(index=False))

    print(f"\nReports saved to: {args.output}")


if __name__ == "__main__":
    main()
