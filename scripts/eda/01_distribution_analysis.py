
# research2/scripts/eda/01_distribution_analysis.py

from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "market_data"
    / "moex_daily_adjusted.sqlite"
)

TABLE_NAME = "adjusted_daily_bars"

OUTPUT_DIR = (
    PROJECT_ROOT
    / "reports"
    / "eda"
    / "01_distribution_analysis"
)

FIGURES_DIR = OUTPUT_DIR / "figures"
TABLES_DIR = OUTPUT_DIR / "tables"

APPROVED_BOARDS = ["TQBR", "EQBR"]

ANALYSIS_START_DATE = "2005-01-01"

MIN_TICKER_OBSERVATIONS = 252

EXTREME_QUANTILES = {
    "top_0_1pct": 0.999,
    "top_1pct": 0.99,
}

PLOT_MAX_POINTS = 200_000

VARIABLES = [
    "returns",
    "absolute_returns",
    "squared_returns",
    "high_low_range",
    "open_close_body",
    "overnight_gap",
    "volume",
    "log_volume",
    "traded_value",
    "log_traded_value",
]


# ============================================================
# PATHS
# ============================================================

def create_directories() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# DATA LOADING
# ============================================================

def load_data() -> pd.DataFrame:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    query = f"""
        SELECT
            secid,
            tradedate,
            adjusted_open AS open,
            adjusted_high AS high,
            adjusted_low AS low,
            adjusted_close AS close,
            adjusted_volume AS volume,
            value,
            boardid
        FROM {TABLE_NAME}
        WHERE boardid IN ({",".join(["?"] * len(APPROVED_BOARDS))})
          AND tradedate >= ?
        ORDER BY secid, tradedate
    """

    with sqlite3.connect(DB_PATH) as connection:
        df = pd.read_sql_query(
            query,
            connection,
            params=[
                *APPROVED_BOARDS,
                ANALYSIS_START_DATE,
            ],
        )

    if df.empty:
        raise RuntimeError(
            "No observations loaded for the selected period."
        )

    df["tradedate"] = pd.to_datetime(
        df["tradedate"],
        errors="coerce",
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "value",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(
        subset=["secid", "tradedate"]
    )

    df["board_priority"] = (
        df["boardid"]
        .map({"TQBR": 0, "EQBR": 1})
        .fillna(99)
    )

    df = (
        df.sort_values(
            ["secid", "tradedate", "board_priority"]
        )
        .drop_duplicates(
            subset=["secid", "tradedate"],
            keep="first",
        )
        .drop(columns=["board_priority"])
        .reset_index(drop=True)
    )

    return df


# ============================================================
# PRE-START CLOSES
# ============================================================

def load_previous_closes() -> pd.DataFrame:
    """
    Load the last available close before the analysis start
    for each ticker.

    This allows calculating the first in-period return
    without including pre-2005 observations in EDA statistics.
    """

    query = f"""
        WITH ranked AS (
            SELECT
                secid,
                tradedate,
                adjusted_close AS previous_close,
                ROW_NUMBER() OVER (
                    PARTITION BY secid
                    ORDER BY tradedate DESC
                ) AS rn
            FROM {TABLE_NAME}
            WHERE boardid IN ({",".join(["?"] * len(APPROVED_BOARDS))})
              AND tradedate < ?
        )
        SELECT
            secid,
            previous_close
        FROM ranked
        WHERE rn = 1
    """

    with sqlite3.connect(DB_PATH) as connection:
        previous = pd.read_sql_query(
            query,
            connection,
            params=[
                *APPROVED_BOARDS,
                ANALYSIS_START_DATE,
            ],
        )

    previous["previous_close"] = pd.to_numeric(
        previous["previous_close"],
        errors="coerce",
    )

    return previous


# ============================================================
# FEATURE ENGINEERING
# ============================================================

def calculate_features(
    df: pd.DataFrame,
    previous_closes: pd.DataFrame,
) -> pd.DataFrame:

    df = df.sort_values(
        ["secid", "tradedate"]
    ).reset_index(drop=True)

    grouped = df.groupby(
        "secid",
        sort=False,
    )

    previous_close = grouped["close"].shift(1)

    # For the first in-period observation, use the last
    # available pre-period close if it exists.
    first_in_period = (
        df.groupby("secid").cumcount() == 0
    )

    previous_close_map = (
        previous_closes
        .set_index("secid")["previous_close"]
    )

    pre_period_close = df["secid"].map(
        previous_close_map
    )

    previous_close = previous_close.where(
        ~first_in_period,
        pre_period_close,
    )

    valid_current_close = df["close"].where(
        df["close"] > 0
    )

    valid_previous_close = previous_close.where(
        previous_close > 0
    )

    valid_open = df["open"].where(
        df["open"] > 0
    )

    valid_high = df["high"].where(
        df["high"] > 0
    )

    valid_low = df["low"].where(
        df["low"] > 0
    )

    df["returns"] = (
        valid_current_close.div(
            valid_previous_close
        ) - 1
    )

    df["absolute_returns"] = df["returns"].abs()

    df["squared_returns"] = df["returns"] ** 2

    df["high_low_range"] = (
        valid_high.div(valid_low) - 1
    )

    df["open_close_body"] = (
        valid_current_close.div(valid_open) - 1
    )

    df["overnight_gap"] = (
        valid_open.div(valid_previous_close) - 1
    )

    # Volume and traded value.
    df["traded_value"] = df["value"]

    df["log_volume"] = np.log1p(
        df["volume"].clip(lower=0)
    )

    df["log_traded_value"] = np.log1p(
        df["traded_value"].clip(lower=0)
    )

    return df


# ============================================================
# SOURCE SUMMARY
# ============================================================

def create_source_summary(
    df: pd.DataFrame,
) -> pd.DataFrame:

    summary = {
        "database": str(DB_PATH),
        "table": TABLE_NAME,
        "analysis_start_date": ANALYSIS_START_DATE,
        "first_date": df["tradedate"].min(),
        "last_date": df["tradedate"].max(),
        "market_dates": df["tradedate"].nunique(),
        "tickers": df["secid"].nunique(),
        "rows": len(df),
        "boards": ", ".join(
            sorted(df["boardid"].dropna().unique())
        ),
    }

    result = pd.DataFrame(
        list(summary.items()),
        columns=["metric", "value"],
    )

    result.to_csv(
        TABLES_DIR / "source_summary.csv",
        index=False,
    )

    return result


# ============================================================
# NON-FINITE DIAGNOSTICS
# ============================================================

def create_nonfinite_diagnostics(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for column in VARIABLES:
        values = pd.to_numeric(
            df[column],
            errors="coerce",
        ).to_numpy(dtype=float)

        rows.append({
            "variable": column,
            "observations": len(values),
            "finite": int(np.isfinite(values).sum()),
            "nan": int(np.isnan(values).sum()),
            "positive_inf": int(np.isposinf(values).sum()),
            "negative_inf": int(np.isneginf(values).sum()),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "nonfinite_diagnostics.csv",
        index=False,
    )

    return result


def create_price_operand_diagnostics(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for column in ["open", "high", "low", "close"]:
        values = df[column]

        rows.append({
            "variable": column,
            "negative": int((values < 0).sum()),
            "zero": int((values == 0).sum()),
            "positive": int((values > 0).sum()),
            "nan": int(values.isna().sum()),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "price_operand_diagnostics.csv",
        index=False,
    )

    return result


# ============================================================
# DISTRIBUTION STATISTICS
# ============================================================

def calculate_distribution_statistics(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for column in VARIABLES:
        values = pd.to_numeric(
            df[column],
            errors="coerce",
        )

        values = values[
            np.isfinite(values)
        ]

        if values.empty:
            continue

        median = values.median()
        mad = (values - median).abs().median()

        quantiles = values.quantile([
            0.01,
            0.05,
            0.25,
            0.50,
            0.75,
            0.95,
            0.99,
        ])

        rows.append({
            "variable": column,
            "n": len(values),
            "mean": values.mean(),
            "std": values.std(),
            "median": median,
            "mad": mad,
            "robust_sigma": 1.4826 * mad,
            "skew": values.skew(),
            "kurtosis_excess": values.kurt(),
            "min": values.min(),
            "q01": quantiles.loc[0.01],
            "q05": quantiles.loc[0.05],
            "q25": quantiles.loc[0.25],
            "q50": quantiles.loc[0.50],
            "q75": quantiles.loc[0.75],
            "q95": quantiles.loc[0.95],
            "q99": quantiles.loc[0.99],
            "max": values.max(),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "pooled_distribution_statistics.csv",
        index=False,
    )

    return result


# ============================================================
# WITHIN / BETWEEN DECOMPOSITION
# ============================================================

def calculate_within_between(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for column in [
        "returns",
        "absolute_returns",
    ]:
        data = df[["secid", column]].copy()

        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

        data = data[
            np.isfinite(data[column])
        ].dropna(subset=["secid"])

        if data.empty:
            continue

        group_means = (
            data.groupby("secid")[column]
            .transform("mean")
        )

        grand_mean = data[column].mean()

        within_ss = (
            (data[column] - group_means) ** 2
        ).sum()

        ticker_means = (
            data.groupby("secid")[column].mean()
        )

        ticker_counts = (
            data.groupby("secid")[column].count()
        )

        between_ss = (
            ticker_counts
            * (ticker_means - grand_mean) ** 2
        ).sum()

        total_ss = within_ss + between_ss

        rows.append({
            "variable": column,
            "observations": len(data),
            "tickers": data["secid"].nunique(),
            "grand_mean": grand_mean,
            "within_ss": within_ss,
            "between_ss": between_ss,
            "total_ss": total_ss,
            "within_share": (
                within_ss / total_ss
                if total_ss > 0 else np.nan
            ),
            "between_share": (
                between_ss / total_ss
                if total_ss > 0 else np.nan
            ),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "within_between_decomposition.csv",
        index=False,
    )

    return result


# ============================================================
# TICKER-LEVEL STATISTICS
# ============================================================

def calculate_ticker_statistics(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    rows = []

    for secid, group in df.groupby("secid"):
        returns = pd.to_numeric(
            group["returns"],
            errors="coerce",
        )

        returns = returns[
            np.isfinite(returns)
        ]

        if returns.empty:
            continue

        absolute_returns = returns.abs()

        rows.append({
            "secid": secid,
            "first_date": group["tradedate"].min(),
            "last_date": group["tradedate"].max(),
            "rows": len(group),
            "valid_returns": len(returns),
            "mean_return": returns.mean(),
            "std_return": returns.std(),
            "median_return": returns.median(),
            "mean_absolute_return": absolute_returns.mean(),
            "median_absolute_return": absolute_returns.median(),
            "q95_absolute_return": absolute_returns.quantile(0.95),
            "q99_absolute_return": absolute_returns.quantile(0.99),
            "skew": returns.skew(),
            "kurtosis_excess": returns.kurt(),
            "mean_volume": group["volume"].mean(),
            "median_volume": group["volume"].median(),
            "mean_traded_value": group["traded_value"].mean(),
            "median_traded_value": group["traded_value"].median(),
        })

    all_stats = pd.DataFrame(rows)

    all_stats.to_csv(
        TABLES_DIR / "ticker_statistics_all.csv",
        index=False,
    )

    eligible = all_stats[
        all_stats["valid_returns"] >= MIN_TICKER_OBSERVATIONS
    ].copy()

    eligible = eligible.sort_values(
        "std_return",
        ascending=False,
    )

    eligible.to_csv(
        TABLES_DIR / "ticker_statistics_min_252.csv",
        index=False,
    )

    return all_stats, eligible


# ============================================================
# EXTREME RETURNS
# ============================================================

def calculate_extreme_returns(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:

    returns = pd.to_numeric(
        df["returns"],
        errors="coerce",
    )

    absolute_returns = returns.abs()

    valid = absolute_returns[
        np.isfinite(absolute_returns)
    ]

    if valid.empty:
        raise RuntimeError(
            "No valid returns available for extreme-return analysis."
        )

    thresholds = {
        name: valid.quantile(quantile)
        for name, quantile in EXTREME_QUANTILES.items()
    }

    rows = []

    for secid, group in df.groupby("secid"):
        ticker_returns = pd.to_numeric(
            group["returns"],
            errors="coerce",
        )

        ticker_returns = ticker_returns[
            np.isfinite(ticker_returns)
        ]

        n = len(ticker_returns)

        if n == 0:
            continue

        ticker_abs = ticker_returns.abs()

        row = {
            "secid": secid,
            "total_observations": n,
            "eligible_for_rate_ranking": (
                n >= MIN_TICKER_OBSERVATIONS
            ),
        }

        for name, threshold in thresholds.items():
            extreme_count = int(
                (ticker_abs >= threshold).sum()
            )

            row[f"{name}_threshold"] = threshold
            row[f"{name}_count"] = extreme_count
            row[f"{name}_rate"] = extreme_count / n

        rows.append(row)

    summary = pd.DataFrame(rows)

    summary.to_csv(
        TABLES_DIR / "extreme_returns_by_ticker.csv",
        index=False,
    )

    eligible = summary[
        summary["eligible_for_rate_ranking"]
    ].copy()

    ranking_frames = []

    for name in thresholds:
        count_column = f"{name}_count"
        rate_column = f"{name}_rate"

        count_rank = eligible.sort_values(
            [count_column, "total_observations"],
            ascending=[False, False],
        ).copy()

        count_rank["ranking_type"] = name
        count_rank["ranking_metric"] = "count"
        count_rank["ranking_value"] = count_rank[count_column]

        rate_rank = eligible.sort_values(
            [rate_column, "total_observations"],
            ascending=[False, False],
        ).copy()

        rate_rank["ranking_type"] = name
        rate_rank["ranking_metric"] = "rate"
        rate_rank["ranking_value"] = rate_rank[rate_column]

        ranking_frames.extend([
            count_rank,
            rate_rank,
        ])

    rankings = (
        pd.concat(ranking_frames, ignore_index=True)
        if ranking_frames
        else pd.DataFrame()
    )

    rankings.to_csv(
        TABLES_DIR / "extreme_ticker_rankings_eligible.csv",
        index=False,
    )

    return summary, rankings


def create_extreme_observation_detail(
    df: pd.DataFrame,
) -> pd.DataFrame:

    returns = pd.to_numeric(
        df["returns"],
        errors="coerce",
    )

    valid_abs = returns.abs()
    valid_abs = valid_abs[
        np.isfinite(valid_abs)
    ]

    threshold_99 = valid_abs.quantile(0.99)

    extreme = df[
        np.isfinite(returns)
        & (returns.abs() >= threshold_99)
    ].copy()

    extreme = extreme.sort_values(
        "returns",
        key=lambda x: x.abs(),
        ascending=False,
    )

    previous_close = (
        df.sort_values(["secid", "tradedate"])
        .groupby("secid")["close"]
        .shift(1)
    )

    previous_close_df = df[
        ["secid", "tradedate"]
    ].copy()

    previous_close_df["previous_close"] = (
        previous_close.to_numpy()
    )

    # First in-period observation can use pre-period close.
    first_in_period = (
        df.groupby("secid").cumcount() == 0
    )

    pre_period_closes = load_previous_closes()

    pre_period_map = (
        pre_period_closes
        .set_index("secid")["previous_close"]
    )

    first_indices = df.index[first_in_period]

    for index in first_indices:
        secid = df.at[index, "secid"]

        if secid in pre_period_map.index:
            previous_close_df.loc[
                index, "previous_close"
            ] = pre_period_map.loc[secid]

    detail = extreme.merge(
        previous_close_df,
        on=["secid", "tradedate"],
        how="left",
    )

    columns = [
        "secid",
        "tradedate",
        "returns",
        "previous_close",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "value",
        "boardid",
    ]

    detail = detail[columns]

    detail.to_csv(
        TABLES_DIR / "extreme_observations_top_1pct.csv",
        index=False,
    )

    return detail


# ============================================================
# VOLUME DISTRIBUTIONS
# ============================================================

def calculate_volume_distributions(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for column in ["volume", "traded_value"]:
        values = pd.to_numeric(
            df[column],
            errors="coerce",
        )

        finite = values[
            np.isfinite(values)
        ]

        positive = finite[finite > 0]
        zero = finite[finite == 0]

        rows.append({
            "variable": column,
            "total_observations": len(finite),
            "zero_count": len(zero),
            "zero_share": (
                len(zero) / len(finite)
                if len(finite) else np.nan
            ),
            "positive_count": len(positive),
            "positive_share": (
                len(positive) / len(finite)
                if len(finite) else np.nan
            ),
            "positive_mean": positive.mean(),
            "positive_median": positive.median(),
            "positive_q25": positive.quantile(0.25),
            "positive_q75": positive.quantile(0.75),
            "positive_q95": positive.quantile(0.95),
            "positive_q99": positive.quantile(0.99),
            "positive_max": positive.max(),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "volume_positive_vs_zero.csv",
        index=False,
    )

    return result


# ============================================================
# PLOTS
# ============================================================

def sample_for_plot(
    values: pd.Series,
    max_points: int = PLOT_MAX_POINTS,
) -> np.ndarray:

    values = pd.to_numeric(
        values,
        errors="coerce",
    )

    values = values[
        np.isfinite(values)
    ].to_numpy()

    if len(values) > max_points:
        rng = np.random.default_rng(42)

        indices = rng.choice(
            len(values),
            size=max_points,
            replace=False,
        )

        values = values[indices]

    return values


def plot_histogram(
    df: pd.DataFrame,
    column: str,
    filename: str,
    title: str,
    log_scale: bool = False,
) -> None:

    values = sample_for_plot(df[column])

    if len(values) == 0:
        return

    if log_scale:
        values = values[values > 0]

    if len(values) == 0:
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(
        values,
        bins=150,
        density=True,
        alpha=0.75,
    )

    if log_scale:
        ax.set_xscale("log")

    ax.set_title(title)
    ax.set_xlabel(column)
    ax.set_ylabel("Density")
    ax.grid(alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / filename,
        dpi=150,
    )

    plt.close(fig)


def plot_return_distribution(
    df: pd.DataFrame,
) -> None:

    values = sample_for_plot(
        df["returns"]
    )

    if len(values) == 0:
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(
        values,
        bins=200,
        density=True,
        alpha=0.75,
    )

    ax.set_title("Daily Return Distribution")
    ax.set_xlabel("Return")
    ax.set_ylabel("Density")
    ax.grid(alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "returns_distribution.png",
        dpi=150,
    )

    plt.close(fig)

    lower = np.quantile(values, 0.005)
    upper = np.quantile(values, 0.995)

    central = values[
        (values >= lower)
        & (values <= upper)
    ]

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(
        central,
        bins=150,
        density=True,
        alpha=0.75,
    )

    ax.set_title(
        "Daily Return Distribution: Central 99%"
    )
    ax.set_xlabel("Return")
    ax.set_ylabel("Density")
    ax.grid(alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "returns_distribution_central_99pct.png",
        dpi=150,
    )

    plt.close(fig)


def plot_ticker_volatility(
    ticker_stats: pd.DataFrame,
) -> None:

    data = ticker_stats[
        np.isfinite(ticker_stats["std_return"])
    ].copy()

    if data.empty:
        return

    data = data.sort_values(
        "std_return",
        ascending=True,
    )

    fig, ax = plt.subplots(figsize=(10, 9))

    ax.barh(
        data["secid"],
        data["std_return"],
    )

    ax.set_title(
        "Ticker-Level Daily Return Volatility "
        f"(n >= {MIN_TICKER_OBSERVATIONS})"
    )

    ax.set_xlabel(
        "Standard Deviation of Daily Returns"
    )

    ax.grid(
        axis="x",
        alpha=0.25,
    )

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "ticker_return_volatility.png",
        dpi=150,
    )

    plt.close(fig)


def create_all_plots(
    df: pd.DataFrame,
    ticker_stats: pd.DataFrame,
) -> None:

    plot_return_distribution(df)

    plot_histogram(
        df,
        "absolute_returns",
        "absolute_returns_distribution.png",
        "Absolute Daily Returns",
    )

    plot_histogram(
        df,
        "squared_returns",
        "squared_returns_distribution.png",
        "Squared Daily Returns",
    )

    plot_histogram(
        df,
        "log_volume",
        "log_volume_distribution.png",
        "Log(1 + Volume) Distribution",
    )

    plot_histogram(
        df,
        "log_traded_value",
        "log_traded_value_distribution.png",
        "Log(1 + Traded Value) Distribution",
    )

    plot_histogram(
        df,
        "high_low_range",
        "high_low_range_distribution.png",
        "High-Low Range Distribution",
    )

    plot_ticker_volatility(ticker_stats)


# ============================================================
# CONSOLE OUTPUT
# ============================================================

def print_section(
    title: str,
    dataframe: pd.DataFrame,
    rows: int = 20,
) -> None:

    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)

    if dataframe.empty:
        print("No results.")
    else:
        print(
            dataframe.head(rows).to_string(
                index=False
            )
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
    )

    create_directories()

    print("\nLoading adjusted market data...")

    df = load_data()

    print(
        f"Loaded {len(df):,} rows | "
        f"{df['secid'].nunique():,} tickers | "
        f"{df['tradedate'].nunique():,} dates"
    )

    print("\nLoading pre-period closes...")

    previous_closes = load_previous_closes()

    print(
        f"Pre-period closes loaded: "
        f"{len(previous_closes):,}"
    )

    print("\nCalculating analytical features...")

    df = calculate_features(
        df,
        previous_closes,
    )

    print("\nCreating source summary...")

    source_summary = create_source_summary(df)

    print("\nCalculating non-finite diagnostics...")

    nonfinite = create_nonfinite_diagnostics(df)

    print("\nCalculating price operand diagnostics...")

    price_diagnostics = create_price_operand_diagnostics(df)

    print("\nCalculating pooled distributions...")

    distributions = calculate_distribution_statistics(df)

    print("\nCalculating within/between decomposition...")

    within_between = calculate_within_between(df)

    print("\nCalculating ticker-level statistics...")

    ticker_stats_all, ticker_stats_eligible = (
        calculate_ticker_statistics(df)
    )

    print("\nCalculating extreme returns...")

    extreme_summary, extreme_rankings = (
        calculate_extreme_returns(df)
    )

    print("\nCreating extreme observation details...")

    extreme_details = create_extreme_observation_detail(df)

    print("\nCalculating volume distributions...")

    volume_distributions = calculate_volume_distributions(df)

    print("\nGenerating visualizations...")

    create_all_plots(
        df,
        ticker_stats_eligible,
    )

    print_section(
        "SOURCE SUMMARY",
        source_summary,
    )

    print_section(
        "NON-FINITE DIAGNOSTICS",
        nonfinite,
    )

    print_section(
        "PRICE OPERAND DIAGNOSTICS",
        price_diagnostics,
    )

    print_section(
        "POOLED DISTRIBUTIONS",
        distributions,
    )

    print_section(
        "WITHIN / BETWEEN DECOMPOSITION",
        within_between,
    )

    print_section(
        "TICKER STATISTICS: MIN 252 RETURNS",
        ticker_stats_eligible.sort_values(
            "std_return",
            ascending=False,
        ),
    )

    print_section(
        "EXTREME TICKER RANKINGS",
        extreme_rankings,
    )

    print_section(
        "EXTREME OBSERVATIONS",
        extreme_details,
    )

    print_section(
        "VOLUME: ZERO VS POSITIVE",
        volume_distributions,
    )

    print("\n" + "=" * 90)
    print("EDA BLOCK 01 COMPLETED")
    print("=" * 90)

    print(f"Analysis period: {ANALYSIS_START_DATE} onward")
    print(f"Figures: {FIGURES_DIR}")
    print(f"Tables:  {TABLES_DIR}")


if __name__ == "__main__":
    main()
