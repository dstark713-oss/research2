# Path: scripts/risk/robustness/strategies.py
# Description: R05 and R06 implementations plus pre-defined parameter grids.

import itertools

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def base_equal_weight_returns(returns):
    return returns.mean(axis=1)


def make_weights(prices, exposure):
    exposure = pd.Series(exposure, index=prices.index)

    return pd.DataFrame(
        np.repeat(
            (exposure / len(prices.columns)).to_numpy()[:, None],
            len(prices.columns),
            axis=1,
        ),
        index=prices.index,
        columns=prices.columns,
    )


def r05_drawdown_weights(
    prices,
    returns,
    dd_medium=0.10,
    dd_high=0.20,
    exposure_medium=0.50,
    exposure_high=0.25,
):
    """
    Self-referential drawdown control.

    Exposure is selected after each close using the drawdown
    of the strategy's own equity curve.
    """

    base_returns = base_equal_weight_returns(returns)

    equity = 1.0
    peak = 1.0
    previous_exposure = 1.0

    exposures = []

    for daily_return in base_returns:
        strategy_return = previous_exposure * daily_return

        equity *= 1.0 + strategy_return
        peak = max(peak, equity)

        drawdown = equity / peak - 1.0

        if drawdown <= -dd_high:
            next_exposure = exposure_high
        elif drawdown <= -dd_medium:
            next_exposure = exposure_medium
        else:
            next_exposure = 1.0

        exposures.append(next_exposure)
        previous_exposure = next_exposure

    return make_weights(
        prices,
        pd.Series(exposures, index=prices.index),
    )


def r06_volatility_weights(
    prices,
    returns,
    vol_window=20,
    reference_window=252,
    threshold_medium=1.50,
    threshold_high=2.00,
    exposure_medium=0.50,
    exposure_high=0.25,
):
    """
    Volatility regime control.

    Short realized volatility of the equal-weight basket is
    compared with its trailing rolling median.
    """

    base_returns = base_equal_weight_returns(returns)

    short_vol = (
        base_returns
        .rolling(
            vol_window,
            min_periods=max(5, vol_window // 2),
        )
        .std()
        * np.sqrt(TRADING_DAYS)
    )

    reference_vol = short_vol.rolling(
        reference_window,
        min_periods=60,
    ).median()

    ratio = short_vol / reference_vol.replace(0.0, np.nan)

    exposure = pd.Series(
        1.0,
        index=prices.index,
        dtype=float,
    )

    exposure.loc[ratio > threshold_medium] = exposure_medium
    exposure.loc[ratio > threshold_high] = exposure_high

    return make_weights(prices, exposure)


def r05_parameter_grid():
    configurations = []

    for dd_medium in [0.075, 0.10, 0.125]:
        for dd_high in [0.15, 0.20, 0.25]:
            if dd_high > dd_medium:
                configurations.append({
                    "dd_medium": dd_medium,
                    "dd_high": dd_high,
                })

    return configurations


def r06_parameter_grid():
    vol_windows = [10, 20, 40]
    reference_windows = [126, 252, 504]

    threshold_pairs = [
        (1.25, 1.75),
        (1.50, 2.00),
        (1.75, 2.25),
    ]

    configurations = []

    for vol_window, reference_window, thresholds in itertools.product(
        vol_windows,
        reference_windows,
        threshold_pairs,
    ):
        configurations.append({
            "vol_window": vol_window,
            "reference_window": reference_window,
            "threshold_medium": thresholds[0],
            "threshold_high": thresholds[1],
        })

    return configurations