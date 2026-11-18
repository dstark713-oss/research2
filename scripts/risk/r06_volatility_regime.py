# Path: scripts/risk/r06_volatility_regime.py
# Description: R06 - volatility regime exposure control.

from common import equal_weights
from config import VOL_WINDOW
from config import VOL_REGIME_MEDIUM, VOL_REGIME_HIGH
from config import TRADING_DAYS


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    portfolio_returns = (
        base.shift(1).fillna(0.0) * returns
    ).sum(axis=1)

    short_vol = (
        portfolio_returns
        .rolling(VOL_WINDOW, min_periods=10)
        .std()
        * TRADING_DAYS ** 0.5
    )

    reference_vol = short_vol.rolling(
        252,
        min_periods=60,
    ).median()

    ratio = short_vol / reference_vol.replace(
        0,
        float("nan"),
    )

    exposure = ratio.copy()
    exposure[:] = 1.0

    exposure.loc[ratio > VOL_REGIME_MEDIUM] = 0.50
    exposure.loc[ratio > VOL_REGIME_HIGH] = 0.25

    return base.mul(exposure.fillna(1.0), axis=0)