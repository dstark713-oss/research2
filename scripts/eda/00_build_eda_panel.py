
# research2/scripts/eda/00_build_eda_panel.py

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data" / "market_data" / "moex_daily_adjusted.sqlite"

UNIVERSE_DIR = ROOT / "data" / "derived" / "historical_universe"
MEMBERSHIP_PATH = UNIVERSE_DIR / "universe_membership.parquet"

OUTPUT_DIR = ROOT / "data" / "derived" / "eda"
PANEL_PATH = OUTPUT_DIR / "eda_panel.parquet"
METADATA_PATH = OUTPUT_DIR / "eda_panel_metadata.json"

REPORT_DIR = ROOT / "reports" / "eda"
REPORT_PATH = REPORT_DIR / "00_EDA_DATASET.md"

TABLE = "adjusted_daily_bars"

START_DATE = "2005-01-01"
BOARDS = ("TQBR", "EQBR")
EXCLUDED_TICKERS = ("GAZC", "GAZS", "GAZT")

SUSPENSION_START = "2022-02-28"
SUSPENSION_END = "2022-03-23"

PIPELINE_VERSION = "1.0.0"


# ============================================================
# HELPERS
# ============================================================

def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def source_fingerprint() -> dict:
    stat = DB_PATH.stat()
    membership_stat = MEMBERSHIP_PATH.stat()

    return {
        "database_size": stat.st_size,
        "database_mtime_ns": stat.st_mtime_ns,
        "membership_size": membership_stat.st_size,
        "membership_mtime_ns": membership_stat.st_mtime_ns,
    }


def config_fingerprint() -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "start_date": START_DATE,
        "boards": list(BOARDS),
        "excluded_tickers": list(EXCLUDED_TICKERS),
        "suspension_start": SUSPENSION_START,
        "suspension_end": SUSPENSION_END,
        "rolling_windows": [5, 20, 60],
    }


def cache_valid() -> bool:
    if not PANEL_PATH.exists() or not METADATA_PATH.exists():
        return False

    try:
        metadata = json.loads(
            METADATA_PATH.read_text(encoding="utf-8")
        )
    except Exception:
        return False

    return (
        metadata.get("source") == source_fingerprint()
        and metadata.get("config") == config_fingerprint()
    )


def atomic_parquet(df: pd.DataFrame, path: Path) -> None:
    temp = path.with_name(path.stem + ".tmp.parquet")
    df.to_parquet(temp, index=False, compression="zstd")
    temp.replace(path)


def atomic_json(data: dict, path: Path) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temp.replace(path)


# ============================================================
# LOAD MEMBERSHIP
# ============================================================

def load_membership() -> pd.DataFrame:
    if not MEMBERSHIP_PATH.exists():
        raise FileNotFoundError(
            f"Universe membership not found: {MEMBERSHIP_PATH}"
        )

    membership = pd.read_parquet(MEMBERSHIP_PATH)

    required = {
        "tradedate",
        "secid",
        "value_60d",
        "rank",
        "universe_size",
        "selection_method",
    }

    missing = required - set(membership.columns)

    if missing:
        raise ValueError(
            f"Membership missing columns: {sorted(missing)}"
        )

    membership["tradedate"] = pd.to_datetime(
        membership["tradedate"]
    )
    membership["secid"] = membership["secid"].astype(str)

    if membership.duplicated(["tradedate", "secid"]).any():
        raise ValueError("Duplicate date/ticker rows in membership.")

    if membership["secid"].isin(EXCLUDED_TICKERS).any():
        raise ValueError("Excluded ticker found in membership.")

    if (membership["universe_size"] > 50).any():
        raise ValueError("Universe contains more than 50 tickers.")

    log(f"Membership rows: {len(membership):,}")
    log(f"Membership tickers: {membership['secid'].nunique():,}")
    log(
        f"Membership dates: "
        f"{membership['tradedate'].nunique():,}"
    )

    return membership


# ============================================================
# LOAD RAW ADJUSTED BARS FOR RELEVANT TICKERS
# ============================================================

def load_source(tickers: list[str]) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    log("Loading adjusted bars for universe tickers...")

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    ticker_placeholders = ",".join("?" for _ in tickers)
    board_placeholders = ",".join("?" for _ in BOARDS)

    query = f"""
        SELECT
            secid,
            tradedate,
            boardid,
            adjusted_open,
            adjusted_high,
            adjusted_low,
            adjusted_close,
            adjusted_volume,
            value
        FROM {TABLE}
        WHERE tradedate >= ?
          AND secid IN ({ticker_placeholders})
          AND boardid IN ({board_placeholders})
        ORDER BY tradedate, secid, boardid
    """

    params = [
        START_DATE,
        *tickers,
        *BOARDS,
    ]

    with sqlite3.connect(DB_PATH) as conn:
        source = pd.read_sql_query(
            query,
            conn,
            params=params,
        )

        calendar_query = f"""
            SELECT DISTINCT tradedate
            FROM {TABLE}
            WHERE tradedate >= ?
              AND boardid IN ({board_placeholders})
            ORDER BY tradedate
        """

        calendar = pd.read_sql_query(
            calendar_query,
            conn,
            params=[START_DATE, *BOARDS],
        )

    if source.empty:
        raise RuntimeError("No source bars found for universe tickers.")

    source["tradedate"] = pd.to_datetime(source["tradedate"])
    source["secid"] = source["secid"].astype(str)
    source["boardid"] = source["boardid"].astype(str)

    calendar["tradedate"] = pd.to_datetime(calendar["tradedate"])

    dates = pd.DatetimeIndex(calendar["tradedate"].dropna().unique())

    dates = dates[
        (dates < pd.Timestamp(SUSPENSION_START))
        | (dates > pd.Timestamp(SUSPENSION_END))
    ]

    source = source[
        ~source["tradedate"].between(
            SUSPENSION_START,
            SUSPENSION_END,
        )
    ].copy()

    numeric_columns = [
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjusted_volume",
        "value",
    ]

    for col in numeric_columns:
        source[col] = pd.to_numeric(
            source[col],
            errors="coerce",
        )

    if source["value"].isna().any():
        raise ValueError("NULL VALUE found in source data.")

    if (source["value"] < 0).any():
        raise ValueError("Negative VALUE found in source data.")

    log(f"Source rows: {len(source):,}")
    log(f"Source tickers: {source['secid'].nunique():,}")
    log(f"Market sessions: {len(dates):,}")

    return source, dates


# ============================================================
# AGGREGATE BOARD DATA
# ============================================================

def aggregate_boards(source: pd.DataFrame) -> pd.DataFrame:
    log("Aggregating board-level observations...")

    keys = ["secid", "tradedate"]

    activity = (
        source.groupby(keys, as_index=False)
        .agg(
            volume=("adjusted_volume", "sum"),
            value=("value", "sum"),
            n_boards=("boardid", "nunique"),
        )
    )

    # TQBR preferred for OHLC; EQBR used as fallback.
    prices = source.copy()

    prices["_priority"] = prices["boardid"].map(
        {"TQBR": 0, "EQBR": 1}
    ).fillna(999)

    prices = (
        prices.sort_values(keys + ["_priority", "boardid"])
        .drop_duplicates(keys, keep="first")
    )

    price_cols = [
        "secid",
        "tradedate",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "boardid",
    ]

    prices = prices[price_cols].rename(
        columns={"boardid": "price_board"}
    )

    result = activity.merge(
        prices,
        on=keys,
        how="left",
        validate="one_to_one",
    )

    if result.duplicated(keys).any():
        raise RuntimeError("Duplicate ticker/date after aggregation.")

    return result


# ============================================================
# FEATURE ENGINEERING
# ============================================================

def build_ticker_features(
    ticker: str,
    ticker_data: pd.DataFrame,
    dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    data = (
        ticker_data
        .set_index("tradedate")
        .sort_index()
        .reindex(dates)
    )

    data.index.name = "tradedate"

    data["secid"] = ticker

    first_observation = ticker_data["tradedate"].min()

    # Before first observed bar, activity is unknown.
    # After first observed bar, missing turnover is zero.
    before_listing = data.index < first_observation

    data.loc[before_listing, ["volume", "value"]] = np.nan

    data["bar_available"] = (
        data["adjusted_close"].notna()
    ).astype("int8")

    data["volume"] = data["volume"].fillna(
        pd.Series(
            np.where(before_listing, np.nan, 0.0),
            index=data.index,
        )
    )

    data["value"] = data["value"].fillna(
        pd.Series(
            np.where(before_listing, np.nan, 0.0),
            index=data.index,
        )
    )

    close = data["adjusted_close"]
    previous_close = close.shift(1)

    # Missing sessions remain missing.
    # No bridging across gaps.
    data["return_1d"] = close.pct_change(fill_method=None)

    data["abs_return"] = data["return_1d"].abs()
    data["squared_return"] = data["return_1d"] ** 2

    data["high_low_range"] = (
        data["adjusted_high"] - data["adjusted_low"]
    ) / previous_close

    data["open_close_body"] = (
        data["adjusted_close"] - data["adjusted_open"]
    ) / data["adjusted_open"]

    data["overnight_gap"] = (
        data["adjusted_open"] / previous_close
    ) - 1.0

    data["log_volume"] = np.log1p(data["volume"].clip(lower=0))
    data["log_value"] = np.log1p(data["value"].clip(lower=0))

    # Realized volatility proxies.
    data["rv_5"] = data["return_1d"].rolling(
        5,
        min_periods=5,
    ).std(ddof=1)

    data["rv_20"] = data["return_1d"].rolling(
        20,
        min_periods=20,
    ).std(ddof=1)

    data["mean_abs_return_5"] = data["abs_return"].rolling(
        5,
        min_periods=5,
    ).mean()

    data["mean_abs_return_20"] = data["abs_return"].rolling(
        20,
        min_periods=20,
    ).mean()

    # Activity features.
    for window in (20, 60):
        data[f"value_sum_{window}"] = data["value"].rolling(
            window,
            min_periods=window,
        ).sum()

        data[f"value_mean_{window}"] = data["value"].rolling(
            window,
            min_periods=window,
        ).mean()

        data[f"volume_sum_{window}"] = data["volume"].rolling(
            window,
            min_periods=window,
        ).sum()

        data[f"volume_mean_{window}"] = data["volume"].rolling(
            window,
            min_periods=window,
        ).mean()

    data["value_to_median_20"] = (
        data["value"]
        / data["value"].rolling(20, min_periods=20).median().replace(0, np.nan)
    )

    data["zero_value_share_20"] = (
        (data["value"] == 0)
        .astype(float)
        .rolling(20, min_periods=20)
        .mean()
    )

    data["zero_volume_share_20"] = (
        (data["volume"] == 0)
        .astype(float)
        .rolling(20, min_periods=20)
        .mean()
    )

    data = data.reset_index()

    return data


def build_feature_panel(
    source_daily: pd.DataFrame,
    dates: pd.DatetimeIndex,
    tickers: list[str],
) -> pd.DataFrame:
    log("Calculating historical features...")

    groups = {
        ticker: group
        for ticker, group in source_daily.groupby("secid")
    }

    frames = []

    for i, ticker in enumerate(tickers, start=1):
        if ticker not in groups:
            continue

        frame = build_ticker_features(
            ticker,
            groups[ticker],
            dates,
        )

        frames.append(frame)

        if i % 50 == 0:
            log(f"Processed {i}/{len(tickers)} tickers")

    if not frames:
        raise RuntimeError("No ticker feature frames were created.")

    result = pd.concat(frames, ignore_index=True)

    return result


# ============================================================
# FILTER TO HISTORICAL UNIVERSE
# ============================================================

def filter_to_universe(
    features: pd.DataFrame,
    membership: pd.DataFrame,
) -> pd.DataFrame:
    log("Filtering features to historical universe...")

    panel = membership.merge(
        features,
        on=["tradedate", "secid"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    missing = int((panel["_merge"] != "both").sum())

    panel["feature_source_available"] = (
        panel["_merge"] == "both"
    ).astype("int8")

    panel = panel.drop(columns="_merge")

    # If no bar exists on the current date, turnover is zero.
    # Prices and price-derived features remain NaN.
    panel["bar_available"] = (
        panel["bar_available"].fillna(0).astype("int8")
    )

    for col in ("volume", "value", "log_volume", "log_value"):
        if col in panel.columns:
            panel.loc[panel["bar_available"] == 0, col] = 0.0

    panel = panel.sort_values(
        ["tradedate", "rank", "secid"]
    ).reset_index(drop=True)

    if panel.duplicated(["tradedate", "secid"]).any():
        raise RuntimeError("Duplicate rows in final EDA panel.")

    if len(panel) != len(membership):
        raise RuntimeError(
            "Filtered panel row count differs from membership."
        )

    log(f"Final panel rows: {len(panel):,}")
    log(f"Tickers: {panel['secid'].nunique():,}")
    log(f"Rows without source feature row: {missing:,}")

    return panel


# ============================================================
# SMOKE TESTS
# ============================================================

def run_smoke_tests(
    panel: pd.DataFrame,
    membership: pd.DataFrame,
) -> dict:
    log("Running smoke tests...")

    tests = {}

    tests["non_empty"] = len(panel) > 0

    tests["unique_date_ticker"] = not panel.duplicated(
        ["tradedate", "secid"]
    ).any()

    tests["membership_row_count_preserved"] = (
        len(panel) == len(membership)
    )

    tests["no_excluded_tickers"] = not panel["secid"].isin(
        EXCLUDED_TICKERS
    ).any()

    tests["universe_size_limit"] = (
        panel["universe_size"].max() <= 50
    )

    tests["date_minimum"] = (
        panel["tradedate"].min() >= pd.Timestamp(START_DATE)
    )

    tests["required_features_present"] = {
        "return_1d",
        "abs_return",
        "squared_return",
        "high_low_range",
        "open_close_body",
        "overnight_gap",
        "rv_5",
        "rv_20",
        "value_sum_20",
        "value_sum_60",
        "volume_sum_20",
        "volume_sum_60",
    }.issubset(panel.columns)

    tests["no_infinite_numeric_values"] = not np.isinf(
        panel.select_dtypes(include=[np.number]).to_numpy(
            dtype=float,
            na_value=np.nan,
        )
    ).any()

    failed = [name for name, passed in tests.items() if not passed]

    for name, passed in tests.items():
        log(f"{'PASS' if passed else 'FAIL'}: {name}")

    if failed:
        raise RuntimeError(
            f"Smoke tests failed: {failed}"
        )

    return tests


# ============================================================
# REPORT
# ============================================================

def write_report(
    panel: pd.DataFrame,
    tests: dict,
) -> None:
    missing = (
        panel.isna()
        .mean()
        .sort_values(ascending=False)
        .rename("missing_share")
        .reset_index()
    )

    lines = [
        "# EDA Dataset",
        "",
        "## Observation unit",
        "",
        "One row represents one ticker on one market session.",
        "The panel contains only ticker/date observations selected",
        "by the point-in-time historical liquidity universe.",
        "",
        "## Source",
        "",
        f"- Database: `{DB_PATH}`",
        f"- Membership: `{MEMBERSHIP_PATH}`",
        f"- Analysis start: `{START_DATE}`",
        f"- Pipeline version: `{PIPELINE_VERSION}`",
        "",
        "## Panel dimensions",
        "",
        f"- Rows: {len(panel):,}",
        f"- Tickers: {panel['secid'].nunique():,}",
        f"- Dates: {panel['tradedate'].nunique():,}",
        f"- First date: {panel['tradedate'].min().date()}",
        f"- Last date: {panel['tradedate'].max().date()}",
        "",
        "## Features",
        "",
    ]

    lines.extend(
        f"- `{column}`"
        for column in panel.columns
    )

    lines.extend(
        [
            "",
            "## Missing values",
            "",
            "| Column | Missing share |",
            "|---|---:|",
        ]
    )

    for _, row in missing.iterrows():
        lines.append(
            f"| `{row['index']}` | {row['missing_share']:.4%} |"
        )

    lines.extend(
        [
            "",
            "## Smoke tests",
            "",
        ]
    )

    for name, passed in tests.items():
        lines.append(
            f"- {'PASS' if passed else 'FAIL'}: `{name}`"
        )

    lines.extend(
        [
            "",
            "## Construction notes",
            "",
            "- Features are calculated using the full available history",
            "  of each ticker before filtering to daily universe membership.",
            "- No forward fill is applied to prices.",
            "- Missing sessions are not bridged when calculating daily returns.",
            "- VALUE and volume are summed across approved boards.",
            "- OHLC is sourced from TQBR where available, otherwise EQBR.",
            "- Rows are retained only when the ticker belongs to the",
            "  historical universe on that date.",
            "- Rolling features are backward-looking.",
            "",
            "## Limitations",
            "",
            "- This is a feature dataset, not a finalized ML matrix.",
            "- Target labels are not calculated in this stage.",
            "- Current-date membership is known after the close.",
            "  For next-session decisions, shift membership by one session.",
            "- Missing feature values are preserved for later analysis.",
            "",
        ]
    )

    REPORT_PATH.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def build(force: bool = False) -> None:
    ensure_dirs()

    if not force and cache_valid():
        log("VALID EDA PANEL CACHE FOUND")
        log(f"Panel: {PANEL_PATH}")
        log("Use --force to rebuild.")
        return

    membership = load_membership()

    tickers = sorted(membership["secid"].unique().tolist())

    source, dates = load_source(tickers)

    source_daily = aggregate_boards(source)

    features = build_feature_panel(
        source_daily,
        dates,
        tickers,
    )

    panel = filter_to_universe(
        features,
        membership,
    )

    tests = run_smoke_tests(
        panel,
        membership,
    )

    log("Writing Parquet cache...")

    atomic_parquet(panel, PANEL_PATH)

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": source_fingerprint(),
        "config": config_fingerprint(),
        "rows": len(panel),
        "tickers": int(panel["secid"].nunique()),
        "dates": int(panel["tradedate"].nunique()),
        "columns": list(panel.columns),
        "smoke_tests": tests,
    }

    atomic_json(metadata, METADATA_PATH)

    write_report(panel, tests)

    log("=" * 60)
    log("EDA PANEL BUILD COMPLETE")
    log("=" * 60)
    log(f"Rows: {len(panel):,}")
    log(f"Tickers: {panel['secid'].nunique():,}")
    log(f"Dates: {panel['tradedate'].nunique():,}")
    log(f"Cache: {PANEL_PATH}")
    log(f"Report: {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force full rebuild.",
    )

    args = parser.parse_args()

    build(force=args.force)


if __name__ == "__main__":
    main()
