
# Path: scripts/risk/r08_market_breadth.py
# Description: R08 - market breadth allocation using MA20.

from common import equal_weights


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    ma20 = prices.rolling(
        window=20,
        min_periods=20,
    ).mean()

    above_ma = prices > ma20

    # Breadth is meaningful only when every basket asset has MA20.
    valid = ma20.notna().all(axis=1)

    breadth = above_ma.mean(axis=1)

    # Neutral exposure during the indicator warm-up period.
    breadth.loc[~valid] = 1.0

    return base.mul(breadth, axis=0)
