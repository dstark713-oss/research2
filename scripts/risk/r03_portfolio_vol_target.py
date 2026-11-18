# Path: scripts/risk/r03_portfolio_vol_target.py
# Description: R03 - portfolio-level volatility targeting.

from common import equal_weights
from config import VOL_WINDOW, PORTFOLIO_VOL_TARGET, TRADING_DAYS


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    base_portfolio_returns = (
        base.shift(1).fillna(0.0) * returns
    ).sum(axis=1)

    realized_vol = (
        base_portfolio_returns
        .rolling(VOL_WINDOW, min_periods=10)
        .std()
        * TRADING_DAYS ** 0.5
    )

    exposure = (
        PORTFOLIO_VOL_TARGET
        / realized_vol.replace(0, float("nan"))
    )

    exposure = exposure.clip(0.0, 1.0).fillna(1.0)

    return base.mul(exposure, axis=0)