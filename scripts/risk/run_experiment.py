
# Path: scripts/risk/run_experiment.py
# Description: Run corrected MOEX portfolio risk-control experiment.

import argparse
from pathlib import Path

import pandas as pd

from config import DB_PATH, OUTPUT_DIR, COST_BPS
from data import load_prices
from common import apply_monthly_rebalance

from engine import (
    backtest,
    buy_and_hold_weights,
    individual_buy_and_hold_weights,
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
        description="Run corrected MOEX risk-control experiment."
    )

    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--cost-bps", type=float, default=COST_BPS)

    return parser.parse_args()


def main():
    args = parse_args()

    # Load the complete available history first.
    all_prices = load_prices(args.db)

    all_returns = (
        all_prices
        .pct_change(fill_method=None)
        .replace([float("inf"), float("-inf")], float("nan"))
        .fillna(0.0)
    )

    # Build all signals on full history to preserve warm-up observations.
    all_strategy_weights = {}

    for name, strategy_function in STRATEGIES.items():
        print(f"Building signals: {name}")

        weights = strategy_function(
            all_prices,
            all_returns,
        )

        weights = apply_monthly_rebalance(weights)

        all_strategy_weights[name] = weights

    # Select evaluation period only after signals have been calculated.
    eval_prices = all_prices.copy()

    if args.start_date:
        eval_prices = eval_prices.loc[
            eval_prices.index >= pd.Timestamp(args.start_date)
        ]

    if args.end_date:
        eval_prices = eval_prices.loc[
            eval_prices.index <= pd.Timestamp(args.end_date)
        ]

    if len(eval_prices) < 300:
        raise RuntimeError(
            "Insufficient evaluation history. "
            "At least 300 observations are required."
        )

    if args.cost_bps < 0:
        raise ValueError("--cost-bps cannot be negative.")

    args.output.mkdir(parents=True, exist_ok=True)

    metrics_rows = []
    daily_returns = pd.DataFrame(index=eval_prices.index)
    gross_returns = pd.DataFrame(index=eval_prices.index)
    daily_turnover = pd.DataFrame(index=eval_prices.index)
    daily_costs = pd.DataFrame(index=eval_prices.index)

    def save_result(name, result):
        metrics_rows.append({
            "strategy": name,
            **result["metrics"],
        })

        daily_returns[name] = result["returns"]
        gross_returns[name] = result["gross_returns"]
        daily_turnover[name] = result["turnover"]
        daily_costs[name] = result["costs"]

        result["weights"].to_csv(
            args.output / f"weights_{name.lower()}.csv",
            index_label="tradedate",
        )

    # Equal-weight passive Buy & Hold.
    bh_weights = buy_and_hold_weights(eval_prices)

    bh_result = backtest(
        eval_prices,
        bh_weights,
        cost_bps=args.cost_bps,
    )

    save_result("BASELINE_EQUAL_WEIGHT_BH", bh_result)

    # Individual passive Buy & Hold baselines.
    for ticker in eval_prices.columns:
        weights = individual_buy_and_hold_weights(
            eval_prices,
            ticker,
        )

        result = backtest(
            eval_prices,
            weights,
            cost_bps=args.cost_bps,
        )

        save_result(f"BUY_HOLD_{ticker}", result)

    # Risk-control strategies.
    for name, full_weights in all_strategy_weights.items():
        target_weights = full_weights.reindex(
            eval_prices.index
        )

        result = backtest(
            eval_prices,
            target_weights,
            cost_bps=args.cost_bps,
        )

        save_result(name, result)

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

    gross_returns.to_csv(
        args.output / "gross_returns.csv",
        index_label="tradedate",
    )

    daily_turnover.to_csv(
        args.output / "daily_turnover.csv",
        index_label="tradedate",
    )

    daily_costs.to_csv(
        args.output / "daily_costs.csv",
        index_label="tradedate",
    )

    metadata = {
        "evaluation_start": str(eval_prices.index.min().date()),
        "evaluation_end": str(eval_prices.index.max().date()),
        "observations": len(eval_prices),
        "tickers": list(eval_prices.columns),
        "cost_bps": args.cost_bps,
        "rebalance_frequency": "M",
        "signal_execution": "signal_at_close_t_applied_after_close_t",
        "return_timing": "held_weights_earn_return_t_to_t_plus_1",
        "turnover_definition": (
            "0.5 * sum(abs(target_weights - drifted_weights)); "
            "cash included"
        ),
        "initial_deployment_reported_separately": True,
        "missing_price_policy": "complete_case_no_forward_fill",
        "signals_use_full_available_history": True,
        "source_database": str(args.db),
        "database_modified": False,
    }

    pd.Series(metadata).to_json(
        args.output / "metadata.json",
        force_ascii=False,
        indent=2,
    )

    display_columns = [
        "strategy",
        "CAGR",
        "Sharpe",
        "MDD",
        "annualized_turnover",
        "initial_turnover",
        "average_exposure",
        "average_cash",
        "total_cost_fraction_sum",
    ]

    print("\nRESULTS")
    print(
        metrics[display_columns].to_string(index=False)
    )

    print(f"\nReports saved to: {args.output}")


if __name__ == "__main__":
    main()
