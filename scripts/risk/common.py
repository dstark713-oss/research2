
# Path: scripts/risk/common.py
# Description: Shared mathematical functions for portfolio risk strategies.

import numpy as np
import pandas as pd

from config import TRADING_DAYS


def equal_weights(index, columns):
    n = len(columns)

    if n == 0:
        raise ValueError("No assets supplied.")

    return pd.DataFrame(
        1.0 / n,
        index=index,
        columns=columns,
    )


def rolling_volatility(returns, window=20):
    return (
        returns
        .rolling(window, min_periods=window)
        .std()
        * np.sqrt(TRADING_DAYS)
    )


def capped_normalize(scores, max_weight=None):
    scores = pd.Series(scores, dtype=float).clip(lower=0.0)

    if scores.sum() <= 0:
        scores[:] = 1.0

    scores = scores / scores.sum()

    if max_weight is None:
        return scores

    result = pd.Series(0.0, index=scores.index)
    active = list(scores.index)
    remaining = 1.0

    while active and remaining > 1e-12:
        active_scores = scores.loc[active]

        if active_scores.sum() <= 0:
            proposed = pd.Series(
                remaining / len(active),
                index=active,
            )
        else:
            proposed = (
                active_scores / active_scores.sum() * remaining
            )

        over = proposed[proposed > max_weight].index.tolist()

        if not over:
            result.loc[active] = proposed
            break

        for ticker in over:
            result[ticker] = max_weight
            remaining -= max_weight
            active.remove(ticker)

    return result


def allocate_with_caps(caps):
    caps = pd.Series(caps, dtype=float).clip(0.0, 1.0)

    result = pd.Series(0.0, index=caps.index)
    active = list(caps.index)
    remaining = 1.0

    while active and remaining > 1e-12:
        proposed = pd.Series(
            remaining / len(active),
            index=active,
        )

        over = [
            ticker for ticker in active
            if proposed[ticker] > caps[ticker]
        ]

        if not over:
            result.loc[active] = proposed
            break

        for ticker in over:
            result[ticker] = caps[ticker]
            remaining -= caps[ticker]
            active.remove(ticker)

    return result


def apply_monthly_rebalance(weights):
    """
    Keep the first observation as initial deployment.
    Update targets only on the final available observation of each month.
    """

    if weights.empty:
        raise ValueError("Cannot rebalance an empty weight frame.")

    weights = weights.copy().sort_index()

    month_groups = weights.groupby(
        weights.index.to_period("M")
    )

    rebalance_dates = month_groups.tail(1).index

    scheduled = weights.loc[rebalance_dates].copy()

    first_date = weights.index[0]
    scheduled.loc[first_date] = weights.loc[first_date]

    return (
        scheduled
        .sort_index()
        .reindex(weights.index)
        .ffill()
        .fillna(0.0)
    )


def rolling_correlation_stats(returns, window=60):
    columns = returns.columns

    average_pairwise = pd.Series(
        np.nan,
        index=returns.index,
        dtype=float,
    )

    asset_average = pd.DataFrame(
        np.nan,
        index=returns.index,
        columns=columns,
    )

    min_periods = max(20, window // 2)

    for i in range(len(returns)):
        start = max(0, i - window + 1)
        block = returns.iloc[start:i + 1]

        if len(block) < min_periods:
            continue

        corr = block.corr(min_periods=min_periods)
        pair_values = []

        for ticker in columns:
            row = corr.loc[ticker].drop(labels=[ticker]).dropna()

            if not row.empty:
                asset_average.loc[returns.index[i], ticker] = row.mean()
                pair_values.extend(row.tolist())

        if pair_values:
            average_pairwise.loc[returns.index[i]] = np.mean(pair_values)

    return average_pairwise, asset_average
