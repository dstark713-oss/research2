
# research2/scripts/eda/01_zero_activity_analysis.py

from __future__ import annotations

import sqlite3
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
    / "zero_activity"
)

FIGURES_DIR = OUTPUT_DIR / "figures"
TABLES_DIR = OUTPUT_DIR / "tables"

ANALYSIS_START_DATE = "2005-01-01"

APPROVED_BOARDS = ["TQBR", "EQBR"]


# ============================================================
# DIRECTORIES
# ============================================================

def create_directories() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# DATA LOADING
# ============================================================

def load_data() -> pd.DataFrame:

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
        ORDER BY secid, tradedate
    """

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    with sqlite3.connect(DB_PATH) as connection:
        df = pd.read_sql_query(
            query,
            connection,
            params=APPROVED_BOARDS,
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
            ["secid", "tradedate"],
            keep="first",
        )
        .drop(columns=["board_priority"])
        .reset_index(drop=True)
    )

    return df


# ============================================================
# FEATURE CALCULATION
# ============================================================

def calculate_features(
    df: pd.DataFrame,
) -> pd.DataFrame:

    df = df.sort_values(
        ["secid", "tradedate"]
    ).copy()

    grouped = df.groupby(
        "secid",
        sort=False,
    )

    df["previous_close"] = grouped["close"].shift(1)

    df["returns"] = (
        df["close"].where(df["close"] > 0)
        /
        df["previous_close"].where(
            df["previous_close"] > 0
        )
        - 1
    )

    df["absolute_returns"] = df["returns"].abs()

    df["zero_return"] = (
        df["returns"].notna()
        & (df["returns"] == 0)
    )

    df["zero_volume"] = (
        df["volume"].notna()
        & (df["volume"] == 0)
    )

    df["positive_volume"] = (
        df["volume"] > 0
    )

    df["zero_value"] = (
        df["value"].notna()
        & (df["value"] == 0)
    )

    df["positive_value"] = (
        df["value"] > 0
    )

    df["ohlc_flat"] = (
        df[["open", "high", "low", "close"]]
        .notna()
        .all(axis=1)
        &
        (
            df["open"] == df["high"]
        )
        &
        (
            df["high"] == df["low"]
        )
        &
        (
            df["low"] == df["close"]
        )
    )

    df["close_unchanged"] = (
        df["close"].notna()
        & df["previous_close"].notna()
        & (df["close"] == df["previous_close"])
    )

    df["ohlc_equals_previous_close"] = (
        df["ohlc_flat"]
        & df["close_unchanged"]
    )

    df["log_volume"] = np.log1p(
        df["volume"].clip(lower=0)
    )

    df["log_value"] = np.log1p(
        df["value"].clip(lower=0)
    )

    # Keep pre-period rows only for calculating the first
    # in-period return. They do not enter the analysis.
    df = df[
        df["tradedate"] >= ANALYSIS_START_DATE
    ].copy()

    return df.reset_index(drop=True)


# ============================================================
# GENERAL SUMMARY
# ============================================================

def calculate_activity_summary(
    df: pd.DataFrame,
) -> pd.DataFrame:

    total = len(df)

    conditions = {
        "zero_volume": df["zero_volume"],
        "positive_volume": df["positive_volume"],
        "zero_value": df["zero_value"],
        "positive_value": df["positive_value"],
        "zero_return": df["zero_return"],
        "ohlc_flat": df["ohlc_flat"],
        "close_unchanged": df["close_unchanged"],
        "ohlc_equals_previous_close": (
            df["ohlc_equals_previous_close"]
        ),
    }

    rows = []

    for name, condition in conditions.items():
        count = int(condition.sum())

        rows.append({
            "condition": name,
            "count": count,
            "share_of_all_rows": count / total,
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "activity_summary.csv",
        index=False,
    )

    return result


# ============================================================
# JOINT ACTIVITY TABLE
# ============================================================

def calculate_joint_activity(
    df: pd.DataFrame,
) -> pd.DataFrame:

    data = df[
        df["volume"].notna()
        & df["value"].notna()
    ].copy()

    data["volume_state"] = np.select(
        [
            data["volume"] == 0,
            data["volume"] > 0,
        ],
        [
            "zero",
            "positive",
        ],
        default="missing_or_negative",
    )

    data["value_state"] = np.select(
        [
            data["value"] == 0,
            data["value"] > 0,
        ],
        [
            "zero",
            "positive",
        ],
        default="missing_or_negative",
    )

    counts = pd.crosstab(
        data["volume_state"],
        data["value_state"],
    )

    shares = pd.crosstab(
        data["volume_state"],
        data["value_state"],
        normalize="all",
    )

    counts.to_csv(
        TABLES_DIR / "volume_value_joint_counts.csv"
    )

    shares.to_csv(
        TABLES_DIR / "volume_value_joint_shares.csv"
    )

    return counts


# ============================================================
# ZERO RETURN ANALYSIS
# ============================================================

def calculate_zero_return_analysis(
    df: pd.DataFrame,
) -> pd.DataFrame:

    valid = df[
        df["returns"].notna()
    ].copy()

    groups = {
        "all": valid,
        "zero_volume": valid[
            valid["volume"] == 0
        ],
        "positive_volume": valid[
            valid["volume"] > 0
        ],
        "zero_value": valid[
            valid["value"] == 0
        ],
        "positive_value": valid[
            valid["value"] > 0
        ],
    }

    rows = []

    for name, group in groups.items():

        returns = group["returns"]
        abs_returns = group["absolute_returns"]

        rows.append({
            "group": name,
            "observations": len(group),
            "zero_return_count": int(
                group["zero_return"].sum()
            ),
            "zero_return_share": (
                group["zero_return"].mean()
                if len(group) else np.nan
            ),
            "mean_absolute_return": abs_returns.mean(),
            "median_absolute_return": abs_returns.median(),
            "q90_absolute_return": abs_returns.quantile(0.90),
            "q95_absolute_return": abs_returns.quantile(0.95),
            "q99_absolute_return": abs_returns.quantile(0.99),
            "mean_return": returns.mean(),
            "std_return": returns.std(),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "zero_return_by_activity.csv",
        index=False,
    )

    return result


# ============================================================
# FLAT PRICE ANALYSIS
# ============================================================

def calculate_flat_price_analysis(
    df: pd.DataFrame,
) -> pd.DataFrame:

    groups = {
        "all": df,
        "zero_volume": df[
            df["volume"] == 0
        ],
        "positive_volume": df[
            df["volume"] > 0
        ],
        "zero_value": df[
            df["value"] == 0
        ],
        "positive_value": df[
            df["value"] > 0
        ],
    }

    rows = []

    for name, group in groups.items():

        rows.append({
            "group": name,
            "observations": len(group),
            "flat_ohlc_count": int(
                group["ohlc_flat"].sum()
            ),
            "flat_ohlc_share": (
                group["ohlc_flat"].mean()
                if len(group) else np.nan
            ),
            "unchanged_close_count": int(
                group["close_unchanged"].sum()
            ),
            "unchanged_close_share": (
                group["close_unchanged"].mean()
                if len(group) else np.nan
            ),
            "ohlc_equals_previous_close_count": int(
                group["ohlc_equals_previous_close"].sum()
            ),
            "ohlc_equals_previous_close_share": (
                group["ohlc_equals_previous_close"].mean()
                if len(group) else np.nan
            ),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "flat_price_by_activity.csv",
        index=False,
    )

    return result


# ============================================================
# ZERO RETURN STREAKS
# ============================================================

def calculate_zero_return_streaks(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for secid, group in df.groupby("secid"):

        group = group.sort_values(
            "tradedate"
        ).copy()

        is_zero = (
            group["zero_return"]
            .fillna(False)
            .astype(bool)
        )

        streak_id = (
            is_zero.ne(is_zero.shift())
            .cumsum()
        )

        zero_streaks = (
            group[is_zero]
            .assign(
                streak_id=streak_id[is_zero].to_numpy()
            )
            .groupby("streak_id")
            .agg(
                streak_length=("tradedate", "size"),
                start_date=("tradedate", "min"),
                end_date=("tradedate", "max"),
                zero_volume_share=("zero_volume", "mean"),
                zero_value_share=("zero_value", "mean"),
                mean_volume=("volume", "mean"),
                mean_value=("value", "mean"),
            )
            .reset_index(drop=True)
        )

        for _, streak in zero_streaks.iterrows():
            rows.append({
                "secid": secid,
                **streak.to_dict(),
            })

    result = pd.DataFrame(rows)

    if not result.empty:
        result = result.sort_values(
            ["streak_length", "secid"],
            ascending=[False, True],
        )

    result.to_csv(
        TABLES_DIR / "zero_return_streaks.csv",
        index=False,
    )

    return result


# ============================================================
# TICKER-LEVEL ACTIVITY
# ============================================================

def calculate_ticker_activity(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rows = []

    for secid, group in df.groupby("secid"):

        valid_returns = group[
            group["returns"].notna()
        ]

        rows.append({
            "secid": secid,
            "observations": len(group),
            "valid_returns": len(valid_returns),
            "zero_volume_share": (
                group["zero_volume"].mean()
            ),
            "zero_value_share": (
                group["zero_value"].mean()
            ),
            "zero_return_share": (
                valid_returns["zero_return"].mean()
                if len(valid_returns) else np.nan
            ),
            "zero_return_with_zero_value_share": (
                (
                    valid_returns["zero_return"]
                    & valid_returns["zero_value"]
                ).mean()
                if len(valid_returns) else np.nan
            ),
            "median_volume": group["volume"].median(),
            "mean_volume": group["volume"].mean(),
            "median_value": group["value"].median(),
            "mean_value": group["value"].mean(),
            "q25_value": group["value"].quantile(0.25),
            "q75_value": group["value"].quantile(0.75),
            "q95_value": group["value"].quantile(0.95),
        })

    result = pd.DataFrame(rows)

    result.to_csv(
        TABLES_DIR / "ticker_activity_statistics.csv",
        index=False,
    )

    return result


# ============================================================
# PLOTS
# ============================================================

def plot_absolute_returns_by_activity(
    df: pd.DataFrame,
) -> None:

    groups = [
        (
            "Zero traded value",
            df.loc[
                (df["value"] == 0)
                & df["absolute_returns"].notna(),
                "absolute_returns",
            ],
        ),
        (
            "Positive traded value",
            df.loc[
                (df["value"] > 0)
                & df["absolute_returns"].notna(),
                "absolute_returns",
            ],
        ),
    ]

    fig, ax = plt.subplots(figsize=(10, 6))

    data = [
        values.to_numpy()
        for _, values in groups
    ]

    ax.boxplot(
        data,
        tick_labels=[
            name for name, _ in groups
        ],
        showfliers=False,
    )

    ax.set_title(
        "Absolute Returns by Traded Value"
    )

    ax.set_ylabel("Absolute Daily Return")
    ax.grid(axis="y", alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "absolute_returns_by_value.png",
        dpi=150,
    )

    plt.close(fig)


def plot_value_distribution(
    df: pd.DataFrame,
) -> None:

    values = df.loc[
        df["value"] > 0,
        "value",
    ]

    if values.empty:
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.hist(
        np.log10(values),
        bins=100,
        alpha=0.75,
    )

    ax.set_title(
        "Distribution of Positive Traded Value"
    )

    ax.set_xlabel("log10(Traded Value)")
    ax.set_ylabel("Observations")
    ax.grid(alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "positive_value_distribution.png",
        dpi=150,
    )

    plt.close(fig)


def plot_value_vs_absolute_return(
    df: pd.DataFrame,
) -> None:

    data = df[
        (df["value"] > 0)
        & df["absolute_returns"].notna()
    ].copy()

    if data.empty:
        return

    if len(data) > 100_000:
        data = data.sample(
            100_000,
            random_state=42,
        )

    fig, ax = plt.subplots(figsize=(10, 6))

    ax.scatter(
        np.log10(data["value"]),
        data["absolute_returns"],
        alpha=0.15,
        s=5,
    )

    ax.set_title(
        "Traded Value vs Absolute Daily Return"
    )

    ax.set_xlabel("log10(Traded Value)")
    ax.set_ylabel("Absolute Daily Return")
    ax.grid(alpha=0.25)

    fig.tight_layout()

    fig.savefig(
        FIGURES_DIR / "value_vs_absolute_return.png",
        dpi=150,
    )

    plt.close(fig)


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    create_directories()

    print("Loading data...")

    df = load_data()

    print(
        f"Loaded {len(df):,} rows, "
        f"{df['secid'].nunique():,} tickers"
    )

    print("Calculating features...")

    df = calculate_features(df)

    print(
        f"Analysis period: {ANALYSIS_START_DATE} onward"
    )

    print(
        f"Rows: {len(df):,} | "
        f"Tickers: {df['secid'].nunique():,} | "
        f"Dates: {df['tradedate'].nunique():,}"
    )

    print("\nCalculating activity summary...")

    activity = calculate_activity_summary(df)

    print("\nCalculating volume/value joint distribution...")

    joint = calculate_joint_activity(df)

    print("\nCalculating zero-return analysis...")

    zero_returns = calculate_zero_return_analysis(df)

    print("\nCalculating flat-price analysis...")

    flat_prices = calculate_flat_price_analysis(df)

    print("\nCalculating zero-return streaks...")

    streaks = calculate_zero_return_streaks(df)

    print("\nCalculating ticker-level activity...")

    ticker_activity = calculate_ticker_activity(df)

    print("\nGenerating plots...")

    plot_absolute_returns_by_activity(df)
    plot_value_distribution(df)
    plot_value_vs_absolute_return(df)

    print("\n" + "=" * 90)
    print("ACTIVITY SUMMARY")
    print("=" * 90)
    print(activity.to_string(index=False))

    print("\n" + "=" * 90)
    print("VOLUME / VALUE JOINT COUNTS")
    print("=" * 90)
    print(joint.to_string())

    print("\n" + "=" * 90)
    print("ZERO RETURNS BY ACTIVITY")
    print("=" * 90)
    print(zero_returns.to_string(index=False))

    print("\n" + "=" * 90)
    print("FLAT PRICES BY ACTIVITY")
    print("=" * 90)
    print(flat_prices.to_string(index=False))

    print("\n" + "=" * 90)
    print("LONGEST ZERO-RETURN STREAKS")
    print("=" * 90)

    if not streaks.empty:
        print(
            streaks.head(30).to_string(index=False)
        )
    else:
        print("No zero-return streaks found.")

    print("\n" + "=" * 90)
    print("TICKER ACTIVITY: HIGHEST ZERO-VALUE SHARE")
    print("=" * 90)

    print(
        ticker_activity.sort_values(
            "zero_value_share",
            ascending=False,
        ).head(30).to_string(index=False)
    )

    print("\n" + "=" * 90)
    print("ANALYSIS COMPLETED")
    print("=" * 90)

    print(f"Tables:  {TABLES_DIR}")
    print(f"Figures: {FIGURES_DIR}")


if __name__ == "__main__":
    main()
