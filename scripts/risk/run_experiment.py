# Path: scripts/risk/run_experiment.py
# Description: Run the initial portfolio risk-control experiment.

import argparse
import sys

from pathlib import Path

import pandas as pd

from config import (
    DB_PATH,
    OUTPUT_DIR,
    COST_BPS,
)

from data import load_prices

from common import apply_monthly_rebalance, equal_weights

from engine import (
    backtest,
    buy_and_hold_weights,
    individual_buy_and_hold_weights,
    calculate_metrics,
)

import r01_equal_weight
import r02_inverse_volatility
import r03_portfolio_vol_target
import r04_volatility_cap
import r05_drawdown_control
import r06_volatility_regime
import r07_correlation_control
import r08_market_breadth
import r09_correlation_weighted


STRATEGIES = {
    "R01_EQUAL_WEIGHT": r01_equal_weight.build_weights,
    "R02_INVERSE_VOL": r02_inverse_volatility.build_weights,
    "R03_PORTFOLIO_VOL_TARGET": r03_portfolio_vol_target.build_weights,
    "R04_VOLATILITY_CAP": r04_volatility_cap.build_weights,
    "R05_DRAWDOWN_CONTROL": r05_drawdown_control.build_weights,
    "R06_VOLATILITY_REGIME": r06_volatility_regime.build_weights,
    "R07_CORRELATION_CONTROL": r07_correlation_control.build_weights,
    "R08_MARKET_BREADTH": r08_market_breadth.build_weights,
    "R09_CORRELATION_WEIGHTED": r09_correlation_weighted.build_weights,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run MOEX portfolio risk-control experiment."
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
    )

    parser.add_argument(
        "--start-date",
        default=None,
    )

    parser.add_argument(
        "--end-date",
        default=None,
    )

    parser.add_argument(
        "--cost-bps",
        type=float,
        default=COST_BPS,
    )

    return parser.parse_args()


def main():
    args = parse_args()

    prices = load_prices(args.db)

    if args.start_date:
        prices = prices.loc[
            prices.index >= pd.Timestamp(args.start_date)
        ]

    if args.end_date:
        prices = prices.loc[
            prices.index <= pd.Timestamp(args.end_date)
        ]

    if len(prices) < 300:
        raise RuntimeError(
            "Insufficient common history. "
            "At least 300 observations are required."
        )

    returns = (
        prices.pct_change()
        .replace([float("inf"), -float("inf")], float("nan"))
        .fillna(0.0)
    )

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics_rows = []
    daily_returns = pd.DataFrame(index=prices.index)

    # ---------------------------------------------------------
    # Baseline 1: Equal-weight Buy & Hold
    # ---------------------------------------------------------

    bh_weights = buy_and_hold_weights(prices)

    bh_result = backtest(
        prices,
        bh_weights,
        cost_bps=args.cost_bps,
    )

    metrics_rows.append({
        "strategy": "BASELINE_EQUAL_WEIGHT_BH",
        **bh_result["metrics"],
    })

    daily_returns["BASELINE_EQUAL_WEIGHT_BH"] = (
        bh_result["returns"]
    )

    # ---------------------------------------------------------
    # Baseline 2: Individual Buy & Hold
    # ---------------------------------------------------------

    for ticker in prices.columns:
        weights = individual_buy_and_hold_weights(
            prices,
            ticker,
        )

        result = backtest(
            prices,
            weights,
            cost_bps=args.cost_bps,
        )

        metrics_rows.append({
            "strategy": f"BUY_HOLD_{ticker}",
            **result["metrics"],
        })

        daily_returns[f"BUY_HOLD_{ticker}"] = (
            result["returns"]
        )

    # ---------------------------------------------------------
    # Risk-control strategies
    # ---------------------------------------------------------

    for name, strategy_function in STRATEGIES.items():
        print(f"Running {name}...")

        target_weights = strategy_function(
            prices,
            returns,
        )

        target_weights = apply_monthly_rebalance(
            target_weights
        )

        result = backtest(
            prices,
            target_weights,
            cost_bps=args.cost_bps,
        )

        metrics_rows.append({
            "strategy": name,
            **result["metrics"],
        })

        daily_returns[name] = result["returns"]

        result["weights"].to_csv(
            args.output / f"weights_{name.lower()}.csv",
            index_label="tradedate",
        )

    # ---------------------------------------------------------
    # Save outputs
    # ---------------------------------------------------------

    metrics = pd.DataFrame(metrics_rows)

    metrics = metrics.sort_values(
        "Sharpe",
        ascending=False,
    )

    metrics.to_csv(
        args.output / "metrics.csv",
        index=False,
    )

    daily_returns.to_csv(
        args.output / "daily_returns.csv",
        index_label="tradedate",
    )

    metadata = {
        "start_date": str(prices.index.min().date()),
        "end_date": str(prices.index.max().date()),
        "observations": len(prices),
        "tickers": list(prices.columns),
        "cost_bps": args.cost_bps,
        "rebalance_frequency": "M",
        "weight_execution": "next_observation",
        "source_database": str(args.db),
        "database_modified": False,
    }

    pd.Series(metadata).to_json(
        args.output / "metadata.json",
        force_ascii=False,
        indent=2,
    )

    print("\nRESULTS")
    print(
        metrics[
            ["strategy", "CAGR", "Sharpe", "MDD",
             "annualized_turnover", "average_exposure"]
        ].to_string(index=False)
    )

    print(f"\nReports saved to: {args.output}")


if __name__ == "__main__":
    main()