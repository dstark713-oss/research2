# Path: scripts/risk/engine.py
# Description: Portfolio backtesting engine and performance metrics.

import numpy as np
import pandas as pd

from config import TRADING_DAYS


def calculate_metrics(returns, turnover=None, weights=None):
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
    mdd = drawdown.min()

    result = {
        "observations": len(returns),
        "CAGR": cagr,
        "Sharpe": sharpe,
        "MDD": mdd,
        "annualized_volatility": volatility * np.sqrt(TRADING_DAYS),
        "total_return": equity.iloc[-1] - 1.0,
    }

    if turnover is not None:
        result["annualized_turnover"] = (
            turnover.mean() * TRADING_DAYS
        )

    if weights is not None:
        result["average_exposure"] = weights.sum(axis=1).mean()

    return result


def backtest(prices, weights, cost_bps=10.0):
    """
    Weights calculated at close t are applied to returns at t+1.
    This prevents same-day look-ahead.

    Turnover is the sum of absolute weight changes.
    Costs are charged on the next observation after a rebalance.
    """
    returns = prices.pct_change().replace(
        [np.inf, -np.inf],
        np.nan,
    ).fillna(0.0)

    weights = weights.reindex(
        index=prices.index,
        columns=prices.columns,
    ).fillna(0.0)

    # No leverage in the initial experiment.
    weights = weights.clip(lower=0.0)

    row_sums = weights.sum(axis=1)
    weights = weights.div(
        row_sums.where(row_sums > 1.0, 1.0),
        axis=0,
    )

    held_weights = weights.shift(1).fillna(0.0)

    gross_returns = (
        held_weights * returns
    ).sum(axis=1)

    turnover = weights.diff().abs().sum(axis=1)
    turnover.iloc[0] = weights.iloc[0].abs().sum()

    trading_costs = (
        turnover * cost_bps / 10000.0
    ).shift(1).fillna(0.0)

    net_returns = gross_returns - trading_costs

    metrics = calculate_metrics(
        net_returns,
        turnover=turnover,
        weights=weights,
    )

    return {
        "returns": net_returns,
        "gross_returns": gross_returns,
        "turnover": turnover,
        "costs": trading_costs,
        "weights": weights,
        "metrics": metrics,
    }


def buy_and_hold_weights(prices):
    """
    Equal initial capital, fixed number of shares thereafter.
    Portfolio weights drift naturally with relative prices.
    """
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
    weights = pd.DataFrame(
        0.0,
        index=prices.index,
        columns=prices.columns,
    )

    weights[ticker] = 1.0

    return weights