# Path: scripts/risk/r09_correlation_weighted.py
# Description: R09 - allocation favoring assets with lower average correlation.

import numpy as np

from common import (
    rolling_correlation_stats,
    capped_normalize,
)

from config import (
    CORR_WINDOW,
    CORR_WEIGHT_LAMBDA,
    CORR_WEIGHT_MAX,
)


def build_weights(prices, returns):
    _, asset_average_corr = rolling_correlation_stats(
        returns,
        window=CORR_WINDOW,
    )

    result = []

    for _, row in asset_average_corr.iterrows():
        row = row.fillna(row.mean())

        if row.isna().all():
            scores = row.copy()
            scores[:] = 1.0
        else:
            scores = np.exp(
                -CORR_WEIGHT_LAMBDA * row
            )

        weights = capped_normalize(
            scores,
            max_weight=CORR_WEIGHT_MAX,
        )

        result.append(weights)

    import pandas as pd

    return pd.DataFrame(
        result,
        index=prices.index,
        columns=prices.columns,
    ).fillna(1.0 / len(prices.columns))