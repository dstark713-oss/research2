# Path: scripts/risk/r08_market_breadth.py
# Description: R08 - market breadth allocation using MA20.

from common import equal_weights


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    ma20 = prices.rolling(
        20,
        min_periods=20,
    ).mean()

    above_ma = prices > ma20

    breadth = above_ma.mean(axis=1)

    # Initial observations use full exposure until MA20 is available.
    breadth = breadth.fillna(1.0)

    return base.mul(breadth, axis=0)