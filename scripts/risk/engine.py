
# Path: scripts/risk/engine.py
# Description: Backtest engine with drift-aware turnover and transaction costs.

import numpy as np
import pandas as pd

from config import TRADING_DAYS


CASH_COLUMN = "__CASH__"


def prepare_target_weights(weights, index, columns):
    target = weights.reindex(
        index=index,
        columns=columns,
    ).fillna(0.0)

    target = target.clip(lower=0.0)

    row_sums = target.sum(axis=1)
    scale = row_sums.where(row_sums > 1.0, 1.0)

    target = target.div(scale, axis=0)

    target[CASH_COLUMN] = (
        1.0 - target.sum(axis=1)
    ).clip(lower=0.0)

    return target


def calculate_metrics(
    returns,
    turnover=None,
    weights=None,
    costs=None,
):
    returns = returns.dropna()

    if returns.empty:
        raise ValueError("Cannot calculate metrics on empty returns.")

    equity = (1.0 + returns).cumprod()

    mean_return = returns.mean()
    volatility = returns.std()

    sharpe = (
        mean_return / volatility * np.sqrt(TRADING_DAYS)
        if volatility > 0
        else np.nan
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
        # Exclude initial deployment from recurring turnover.
        recurring_turnover = turnover.iloc[1:]

        result["initial_turnover"] = (
            float(turnover.iloc[0]) if len(turnover) else 0.0
        )

        result["total_turnover_ex_initial"] = (
            float(recurring_turnover.sum())
        )

        result["annualized_turnover"] = (
            float(recurring_turnover.sum() / years)
            if years > 0 else np.nan
        )

    if weights is not None:
        risky_weights = weights.drop(
            columns=[CASH_COLUMN],
            errors="ignore",
        )

        result["average_exposure"] = (
            risky_weights.sum(axis=1).mean()
        )

        result["average_cash"] = (
            1.0 - result["average_exposure"]
        )

    if costs is not None:
        result["total_cost_fraction_sum"] = float(costs.sum())
        result["average_daily_cost"] = float(costs.mean())

    return result


def backtest(prices, weights, cost_bps=10.0):
    """
    Signals at close t determine target weights at close t.
    Those holdings earn returns from t to t+1.

    At each close:
      1. Existing holdings earn the observed daily return.
      2. Holdings drift with relative asset performance.
      3. Drifted weights are compared with target weights.
      4. Transaction costs are charged on actual traded notional.
      5. Target weights become holdings for the next observation.

    Turnover is one-way traded notional:
        0.5 * sum(abs(target_weight - drifted_weight))

    Cash is an explicit portfolio component.
    """

    if cost_bps < 0:
        raise ValueError("cost_bps cannot be negative.")

    if prices.empty:
        raise ValueError("Prices are empty.")

    if prices.isna().any().any():
        raise ValueError(
            "Prices contain missing values. "
            "The engine requires a complete-case price panel."
        )

    asset_returns = prices.pct_change(fill_method=None)

    # No return is earned before the first portfolio deployment.
    asset_returns.iloc[0] = 0.0

    if asset_returns.isna().any().any():
        raise RuntimeError(
            "Unexpected missing returns after complete-case filtering."
        )

    target = prepare_target_weights(
        weights,
        prices.index,
        prices.columns,
    )

    n = len(prices)
    asset_columns = list(prices.columns)
    full_columns = asset_columns + [CASH_COLUMN]

    current = pd.Series(
        0.0,
        index=full_columns,
    )
    current[CASH_COLUMN] = 1.0

    gross_returns = pd.Series(0.0, index=prices.index)
    net_returns = pd.Series(0.0, index=prices.index)
    turnover = pd.Series(0.0, index=prices.index)
    costs = pd.Series(0.0, index=prices.index)

    for i, date in enumerate(prices.index):
        daily_asset_returns = asset_returns.loc[date]

        # Existing holdings earn today's market return.
        gross_return = float(
            (current[asset_columns] * daily_asset_returns).sum()
        )

        if gross_return <= -1.0:
            raise RuntimeError(
                f"Portfolio return <= -100% on {date.date()}."
            )

        gross_returns.loc[date] = gross_return

        # Drift current holdings to post-market weights.
        denominator = 1.0 + gross_return

        drifted = current.copy()

        drifted[asset_columns] = (
            current[asset_columns]
            * (1.0 + daily_asset_returns)
            / denominator
        )

        drifted[CASH_COLUMN] = (
            current[CASH_COLUMN] / denominator
        )

        # Trade from actual drifted holdings to target holdings.
        desired = target.loc[date, full_columns]

        traded_notional = 0.5 * float(
            (desired - drifted).abs().sum()
        )

        turnover.loc[date] = traded_notional

        cost_rate = cost_bps / 10000.0
        cost_fraction = (
            (1.0 + gross_return)
            * cost_rate
            * traded_notional
        )

        costs.loc[date] = cost_fraction

        net_return = gross_return - cost_fraction
        net_returns.loc[date] = net_return

        # Rebalanced holdings earn the next observation's return.
        current = desired.copy()

    metrics = calculate_metrics(
        net_returns,
        turnover=turnover,
        weights=target,
        costs=costs,
    )

    return {
        "returns": net_returns,
        "gross_returns": gross_returns,
        "turnover": turnover,
        "costs": costs,
        "weights": target,
        "metrics": metrics,
    }


def buy_and_hold_weights(prices):
    """
    Equal initial capital, fixed share quantities thereafter.
    Weights drift naturally with relative prices.
    """

    if prices.empty:
        raise ValueError("Prices are empty.")

    initial_prices = prices.iloc[0]

    shares = (
        1.0 / len(prices.columns)
    ) / initial_prices

    market_values = prices.mul(shares, axis=1)

    return market_values.div(
        market_values.sum(axis=1),
        axis=0,
    )


def individual_buy_and_hold_weights(prices, ticker):
    if ticker not in prices.columns:
        raise KeyError(f"Unknown ticker: {ticker}")

    weights = pd.DataFrame(
        0.0,
        index=prices.index,
        columns=prices.columns,
    )

    weights[ticker] = 1.0

    return weights
