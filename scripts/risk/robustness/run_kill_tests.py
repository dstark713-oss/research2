# Path: scripts/risk/robustness/run_kill_tests.py
# Description: Run pre-defined robustness and falsification tests for R05 and R06.

import argparse
import json
import sys

from pathlib import Path

import pandas as pd


RISK_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RISK_DIR))

from config import DB_PATH, COST_BPS
from data import load_prices

from metrics import (
    simulate_daily,
    equal_weight_targets,
    buy_and_hold_targets,
    calculate_metrics,
)

from strategies import (
    r05_drawdown_weights,
    r06_volatility_weights,
    r05_parameter_grid,
    r06_parameter_grid,
)


PERIODS = [
    ("2014_2017", "2014-01-01", "2017-12-31"),
    ("2018_2020", "2018-01-01", "2020-12-31"),
    ("2021_2023", "2021-01-01", "2023-12-31"),
    ("2024_2026", "2024-01-01", "2026-12-31"),
]

COST_SCENARIOS = [10.0, 20.0, 30.0]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run R05/R06 robustness and kill tests."
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parents[3]
            / "data"
            / "reports"
            / "risk_control"
            / "robustness_kill"
        ),
    )

    parser.add_argument(
        "--cost-bps",
        type=float,
        default=COST_BPS,
    )

    return parser.parse_args()


def run_strategy(prices, targets, cost_bps):
    return simulate_daily(
        prices=prices,
        target_weights=targets,
        cost_bps=cost_bps,
    )


def metrics_row(name, result, **extra):
    return {
        "strategy": name,
        **result["metrics"],
        **extra,
    }


def subset_metrics(result, start, end):
    selected = result["returns"].loc[start:end]

    if len(selected) < 60:
        return None

    return calculate_metrics(
        selected,
        turnover=result["turnover"].loc[selected.index],
        exposure=result["exposure"].loc[selected.index],
        costs=result["costs"].loc[selected.index],
    )


def main():
    args = parse_args()

    if args.cost_bps <= 0:
        raise ValueError("--cost-bps must be positive.")

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    prices = load_prices(args.db)

    if len(prices) < 750:
        raise RuntimeError(
            "Insufficient history for robustness tests."
        )

    returns = (
        prices.pct_change()
        .replace([float("inf"), -float("inf")], float("nan"))
        .fillna(0.0)
    )

    print("\nDATA")
    print(f"Period: {prices.index.min().date()} - "
          f"{prices.index.max().date()}")
    print(f"Observations: {len(prices):,}")
    print(f"Assets: {len(prices.columns)}")
    print(f"Cost scenarios: {COST_SCENARIOS}")

    # ---------------------------------------------------------
    # Base strategies and controls
    # ---------------------------------------------------------

    targets = {
        "BASELINE_EQUAL_WEIGHT_BH": buy_and_hold_targets(
            prices.index,
            prices.columns,
        ),
        "BASELINE_EQUAL_WEIGHT_DAILY": equal_weight_targets(
            prices.index,
            prices.columns,
        ),
        "R05_DRAWDOWN_CONTROL": r05_drawdown_weights(
            prices,
            returns,
        ),
        "R06_VOLATILITY_REGIME": r06_volatility_weights(
            prices,
            returns,
        ),
    }

    base_results = {}

    for name, weights in targets.items():
        print(f"Running base: {name}")

        base_results[name] = run_strategy(
            prices,
            weights,
            args.cost_bps,
        )

    summary_rows = [
        metrics_row(name, result)
        for name, result in base_results.items()
    ]

    daily_returns = pd.DataFrame({
        name: result["returns"]
        for name, result in base_results.items()
    })

    daily_exposure = pd.DataFrame({
        name: result["exposure"]
        for name, result in base_results.items()
    })

    daily_turnover = pd.DataFrame({
        name: result["turnover"]
        for name, result in base_results.items()
    })

    # ---------------------------------------------------------
    # Exposure-matched equal-weight controls
    # ---------------------------------------------------------

    matched_results = {}

    for strategy_name in [
        "R05_DRAWDOWN_CONTROL",
        "R06_VOLATILITY_REGIME",
    ]:
        average_exposure = base_results[
            strategy_name
        ]["metrics"]["average_exposure"]

        matched_name = f"{strategy_name}_MATCHED_EXPOSURE"

        matched_targets = equal_weight_targets(
            prices.index,
            prices.columns,
            exposure=average_exposure,
        )

        result = run_strategy(
            prices,
            matched_targets,
            args.cost_bps,
        )

        matched_results[matched_name] = result

        summary_rows.append(
            metrics_row(
                matched_name,
                result,
                matched_to=strategy_name,
                matched_average_exposure=average_exposure,
            )
        )

        daily_returns[matched_name] = result["returns"]
        daily_exposure[matched_name] = result["exposure"]
        daily_turnover[matched_name] = result["turnover"]

    # ---------------------------------------------------------
    # Cost sensitivity
    # ---------------------------------------------------------

    cost_rows = []

    for cost_bps in COST_SCENARIOS:
        for name in [
            "R05_DRAWDOWN_CONTROL",
            "R06_VOLATILITY_REGIME",
        ]:
            result = run_strategy(
                prices,
                targets[name],
                cost_bps,
            )

            cost_rows.append({
                "strategy": name,
                "cost_bps": cost_bps,
                **result["metrics"],
            })

    cost_sensitivity = pd.DataFrame(cost_rows)

    # ---------------------------------------------------------
    # Temporal decomposition
    # ---------------------------------------------------------

    period_rows = []

    for name in [
        "BASELINE_EQUAL_WEIGHT_BH",
        "BASELINE_EQUAL_WEIGHT_DAILY",
        "R05_DRAWDOWN_CONTROL",
        "R06_VOLATILITY_REGIME",
    ]:
        result = base_results[name]

        for period_name, start, end in PERIODS:
            metrics = subset_metrics(
                result,
                start,
                end,
            )

            if metrics is None:
                continue

            period_rows.append({
                "strategy": name,
                "period": period_name,
                **metrics,
            })

    period_sensitivity = pd.DataFrame(period_rows)

    # ---------------------------------------------------------
    # R05 parameter sensitivity
    # ---------------------------------------------------------

    parameter_rows = []

    for parameters in r05_parameter_grid():
        weights = r05_drawdown_weights(
            prices,
            returns,
            **parameters,
        )

        result = run_strategy(
            prices,
            weights,
            args.cost_bps,
        )

        parameter_rows.append({
            "strategy": "R05_DRAWDOWN_CONTROL",
            **parameters,
            **result["metrics"],
        })

    # ---------------------------------------------------------
    # R06 parameter sensitivity
    # ---------------------------------------------------------

    for parameters in r06_parameter_grid():
        weights = r06_volatility_weights(
            prices,
            returns,
            **parameters,
        )

        result = run_strategy(
            prices,
            weights,
            args.cost_bps,
        )

        parameter_rows.append({
            "strategy": "R06_VOLATILITY_REGIME",
            **parameters,
            **result["metrics"],
        })

    parameter_sensitivity = pd.DataFrame(parameter_rows)

    bh_sharpe = base_results[
        "BASELINE_EQUAL_WEIGHT_BH"
    ]["metrics"]["Sharpe"]

    ew_sharpe = base_results[
        "BASELINE_EQUAL_WEIGHT_DAILY"
    ]["metrics"]["Sharpe"]

    parameter_sensitivity["beats_BH_sharpe"] = (
        parameter_sensitivity["Sharpe"] > bh_sharpe
    )

    parameter_sensitivity["beats_daily_EW_sharpe"] = (
        parameter_sensitivity["Sharpe"] > ew_sharpe
    )

    parameter_summary = (
        parameter_sensitivity
        .groupby("strategy")
        .agg(
            configurations=("Sharpe", "count"),
            median_sharpe=("Sharpe", "median"),
            min_sharpe=("Sharpe", "min"),
            max_sharpe=("Sharpe", "max"),
            median_CAGR=("CAGR", "median"),
            median_MDD=("MDD", "median"),
            share_beating_BH=("beats_BH_sharpe", "mean"),
            share_beating_daily_EW=(
                "beats_daily_EW_sharpe",
                "mean",
            ),
        )
        .reset_index()
    )

    # ---------------------------------------------------------
    # Save outputs
    # ---------------------------------------------------------

    pd.DataFrame(summary_rows).to_csv(
        args.output / "summary.csv",
        index=False,
    )

    cost_sensitivity.to_csv(
        args.output / "cost_sensitivity.csv",
        index=False,
    )

    period_sensitivity.to_csv(
        args.output / "period_sensitivity.csv",
        index=False,
    )

    parameter_sensitivity.to_csv(
        args.output / "parameter_sensitivity.csv",
        index=False,
    )

    parameter_summary.to_csv(
        args.output / "parameter_summary.csv",
        index=False,
    )

    daily_returns.to_csv(
        args.output / "daily_returns.csv",
        index_label="tradedate",
    )

    daily_exposure.to_csv(
        args.output / "daily_exposure.csv",
        index_label="tradedate",
    )

    daily_turnover.to_csv(
        args.output / "daily_turnover.csv",
        index_label="tradedate",
    )

    metadata = {
        "experiment": "R05_R06_ROBUSTNESS_KILL_TESTS",
        "start_date": str(prices.index.min().date()),
        "end_date": str(prices.index.max().date()),
        "observations": len(prices),
        "tickers": list(prices.columns),
        "primary_cost_bps": args.cost_bps,
        "cost_scenarios_bps": COST_SCENARIOS,
        "execution": "daily_after_close",
        "signal_timing": "signal_at_close_t_applies_to_return_t_plus_1",
        "turnover": "half_l1_distance_including_cash",
        "risk_free_rate": 0.0,
        "sharpe": "raw_sharpe_without_risk_free_subtraction",
        "parameter_selection": "no_optimization",
        "database_modified": False,
    }

    with open(
        args.output / "metadata.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            metadata,
            file,
            ensure_ascii=False,
            indent=2,
        )

    # ---------------------------------------------------------
    # Console summary
    # ---------------------------------------------------------

    print("\nBASE RESULTS")
    print(
        pd.DataFrame(summary_rows)[
            [
                "strategy",
                "CAGR",
                "Sharpe",
                "MDD",
                "annualized_turnover",
                "average_exposure",
            ]
        ].to_string(index=False)
    )

    print("\nPARAMETER STABILITY")
    print(parameter_summary.to_string(index=False))

    print(f"\nReports saved to: {args.output}")


if __name__ == "__main__":
    main()