# Path: scripts/risk/robustness/metrics.py
# Description: Daily portfolio simulation and performance metrics for risk robustness tests.

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def calculate_metrics(returns, turnover=None, exposure=None, costs=None):
    returns = pd.Series(returns).dropna()

    if returns.empty:
        raise ValueError("Cannot calculate metrics on empty returns.")

    equity = (1.0 + returns).cumprod()
    volatility = returns.std()

    sharpe = (
        returns.mean() / volatility * np.sqrt(TRADING_DAYS)
        if volatility > 0 else np.nan
    )

    years = len(returns) / TRADING_DAYS

    cagr = (
        equity.iloc[-1] ** (1.0 / years) - 1.0
        if years > 0 and equity.iloc[-1] > 0
        else np.nan
    )

    drawdown = equity / equity.cummax() - 1.0

    result = {
        "observations": len(returns),
        "CAGR": cagr,
        "Sharpe": sharpe,
        "MDD": drawdown.min(),
        "annualized_volatility": volatility * np.sqrt(TRADING_DAYS),
        "total_return": equity.iloc[-1] - 1.0,
    }

    if turnover is not None:
        turnover = pd.Series(turnover).reindex(returns.index)
        result["annualized_turnover"] = (
            turnover.mean() * TRADING_DAYS
        )

    if exposure is not None:
        exposure = pd.Series(exposure).reindex(returns.index)
        result["average_exposure"] = exposure.mean()

    if costs is not None:
        costs = pd.Series(costs).reindex(returns.index)
        result["total_cost_fraction_sum"] = costs.sum()

    return result


def simulate_daily(prices, target_weights, cost_bps=10.0):
    """
    Daily execution model.

    Signal at close t is executed after return t and affects t+1.

    NaN target row:
        Do not rebalance. Existing holdings drift naturally.

    Non-NaN target row:
        Rebalance to the specified risky-asset weights.
        Remaining capital is held as cash.

    Turnover:
        0.5 * L1 distance between drifted and target weights,
        including cash.

    Costs:
        Applied to post-return portfolio value at the rebalance.
    """

    returns = (
        prices.pct_change()
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )

    targets = target_weights.reindex(
        index=prices.index,
        columns=prices.columns,
    ).copy()

    if targets.iloc[0].isna().all():
        raise ValueError("First target row must define initial allocation.")

    if targets.iloc[0].isna().any():
        raise ValueError("Initial target row contains partial NaNs.")

    n_assets = len(prices.columns)

    previous_weights = np.zeros(n_assets, dtype=float)
    previous_cash = 1.0

    net_returns = []
    gross_returns = []
    turnover_values = []
    cost_values = []
    exposure_values = []

    for i, date in enumerate(prices.index):
        daily_returns = returns.iloc[i].to_numpy(dtype=float)

        if i == 0:
            gross_return = 0.0
            drifted_weights = previous_weights.copy()
            drifted_cash = previous_cash
        else:
            gross_return = float(
                np.dot(previous_weights, daily_returns)
            )

            portfolio_growth = 1.0 + gross_return

            if portfolio_growth <= 0:
                raise RuntimeError(
                    f"Non-positive portfolio value on {date}."
                )

            drifted_weights = (
                previous_weights * (1.0 + daily_returns)
                / portfolio_growth
            )

            drifted_cash = previous_cash / portfolio_growth

        target_row = targets.iloc[i]

        if target_row.isna().all():
            target = drifted_weights.copy()
            target_cash = drifted_cash
            turnover = 0.0

        else:
            if target_row.isna().any():
                raise ValueError(
                    f"Partial target weights on {date}."
                )

            target = target_row.to_numpy(dtype=float)

            if not np.isfinite(target).all():
                raise ValueError(
                    f"Non-finite target weights on {date}."
                )

            if (target < -1e-12).any():
                raise ValueError(
                    f"Negative target weight on {date}."
                )

            target = np.clip(target, 0.0, None)

            total_target = target.sum()

            if total_target > 1.0 + 1e-8:
                raise ValueError(
                    f"Target exposure exceeds 100% on {date}: "
                    f"{total_target:.6f}"
                )

            target_cash = max(0.0, 1.0 - total_target)

            turnover = 0.5 * (
                np.abs(target - drifted_weights).sum()
                + abs(target_cash - drifted_cash)
            )

        cost_fraction = turnover * cost_bps / 10000.0

        net_growth = (
            (1.0 + gross_return)
            * (1.0 - cost_fraction)
        )

        net_return = net_growth - 1.0

        net_returns.append(net_return)
        gross_returns.append(gross_return)
        turnover_values.append(turnover)
        cost_values.append(cost_fraction)
        exposure_values.append(target.sum())

        previous_weights = target.copy()
        previous_cash = target_cash

    index = prices.index

    result = {
        "returns": pd.Series(net_returns, index=index),
        "gross_returns": pd.Series(gross_returns, index=index),
        "turnover": pd.Series(turnover_values, index=index),
        "costs": pd.Series(cost_values, index=index),
        "exposure": pd.Series(exposure_values, index=index),
    }

    result["metrics"] = calculate_metrics(
        result["returns"],
        turnover=result["turnover"],
        exposure=result["exposure"],
        costs=result["costs"],
    )

    return result


def equal_weight_targets(index, columns, exposure=1.0):
    n = len(columns)

    if n == 0:
        raise ValueError("No assets supplied.")

    return pd.DataFrame(
        exposure / n,
        index=index,
        columns=columns,
    )


def buy_and_hold_targets(index, columns, exposure=1.0):
    """
    Initial equal allocation only.
    All subsequent rows are NaN, meaning no rebalance.
    """

    targets = pd.DataFrame(
        np.nan,
        index=index,
        columns=columns,
    )

    targets.iloc[0] = exposure / len(columns)

    return targets