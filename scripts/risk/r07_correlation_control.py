# Path: scripts/risk/r07_correlation_control.py
# Description: R07 - portfolio exposure control based on average pairwise correlation.

from common import equal_weights, rolling_correlation_stats
from config import (
    CORR_WINDOW,
    CORR_EXPOSURE_FLOOR,
    CORR_EXPOSURE_CEILING,
    CORR_MIN_EXPOSURE,
)


def build_weights(prices, returns):
    base = equal_weights(
        prices.index,
        prices.columns,
    )

    average_corr, _ = rolling_correlation_stats(
        returns,
        window=CORR_WINDOW,
    )

    denominator = (
        CORR_EXPOSURE_CEILING
        - CORR_EXPOSURE_FLOOR
    )

    stress = (
        average_corr - CORR_EXPOSURE_FLOOR
    ) / denominator

    stress = stress.clip(0.0, 1.0)

    exposure = (
        1.0
        - stress * (1.0 - CORR_MIN_EXPOSURE)
    )

    exposure = exposure.fillna(1.0)

    return base.mul(exposure, axis=0)