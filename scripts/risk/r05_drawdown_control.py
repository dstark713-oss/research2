
# Path: scripts/risk/r05_drawdown_control.py
# Description: R05 - monthly exposure control using its own equity drawdown.

import pandas as pd

from common import equal_weights


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    base_returns = returns.mean(axis=1)

    month_end_dates = set(
        prices.groupby(
            prices.index.to_period("M")
        ).tail(1).index
    )

    exposure = pd.Series(
        1.0,
        index=prices.index,
        dtype=float,
    )

    equity = 1.0
    peak = 1.0
    active_exposure = 1.0

    for date in prices.index:
        # Current exposure was set at the previous rebalance close.
        strategy_return = (
            active_exposure * base_returns.loc[date]
        )

        equity *= 1.0 + strategy_return
        peak = max(peak, equity)

        drawdown = equity / peak - 1.0

        # The new exposure is decided after observing today's close.
        new_exposure = 1.0

        if drawdown <= -0.20:
            new_exposure = 0.25
        elif drawdown <= -0.10:
            new_exposure = 0.50

        if date in month_end_dates:
            active_exposure = new_exposure

        exposure.loc[date] = active_exposure

    return base.mul(exposure, axis=0)
