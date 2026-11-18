# Path: scripts/risk/r02_inverse_volatility.py
# Description: R02 - inverse volatility allocation.

from common import rolling_volatility, capped_normalize
from config import VOL_WINDOW, INVERSE_VOL_MAX_WEIGHT


def build_weights(prices, returns):
    volatility = rolling_volatility(
        returns,
        window=VOL_WINDOW,
    )

    inverse_vol = 1.0 / volatility.replace(0, float("nan"))

    result = inverse_vol.apply(
        lambda row: capped_normalize(
            row.fillna(row.median()).fillna(1.0),
            max_weight=INVERSE_VOL_MAX_WEIGHT,
        ),
        axis=1,
    )

    return result.fillna(1.0 / len(prices.columns))