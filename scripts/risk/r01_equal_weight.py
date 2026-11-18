# Path: scripts/risk/r01_equal_weight.py
# Description: R01 - equal-weight portfolio baseline.

from common import equal_weights


def build_weights(prices, returns):
    return equal_weights(
        prices.index,
        prices.columns,
    )