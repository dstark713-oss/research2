# Path: scripts/risk/r04_volatility_cap.py
# Description: R04 - individual asset volatility caps.

from common import rolling_volatility, allocate_with_caps
from config import VOL_WINDOW, INDIVIDUAL_VOL_CAP


def build_weights(prices, returns):
    volatility = rolling_volatility(
        returns,
        window=VOL_WINDOW,
    )

    result = []

    for _, row in volatility.iterrows():
        caps = INDIVIDUAL_VOL_CAP / row.replace(
            0,
            float("nan"),
        )

        caps = caps.replace(
            [float("inf"), -float("inf")],
            float("nan"),
        ).fillna(1.0)

        weights = allocate_with_caps(caps)
        result.append(weights)

    return result_to_frame(result, prices)


def result_to_frame(result, prices):
    import pandas as pd

    return pd.DataFrame(
        result,
        index=prices.index,
        columns=prices.columns,
    )