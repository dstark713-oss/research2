# Path: scripts/risk/r05_drawdown_control.py
# Description: R05 - portfolio exposure reduction based on drawdown.

from common import equal_weights


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    base_returns = (
        base.shift(1).fillna(0.0) * returns
    ).sum(axis=1)

    equity = (1.0 + base_returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0

    exposure = drawdown.copy()
    exposure[:] = 1.0

    exposure.loc[drawdown <= -0.10] = 0.50
    exposure.loc[drawdown <= -0.20] = 0.25

    return base.mul(exposure, axis=0)