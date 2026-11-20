
# research2/scripts/eda/03_build_historical_universe.py

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# CONFIG
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
    / "data"
    / "derived"
    / "historical_universe"
)

REPORT_DIR = (
    PROJECT_ROOT
    / "reports"
    / "eda"
    / "historical_universe"
)

ANALYSIS_START_DATE = "2005-01-01"

# Universe selection
TOP_N = 50
LOOKBACK_SESSIONS = 60

# Approved trading boards
APPROVED_BOARDS = ("TQBR", "EQBR")

# Explicit exclusions
EXCLUDED_TICKERS = {"GAZC", "GAZS", "GAZT"}

# Trading suspension period excluded from the market calendar
EXCLUDED_DATE_START = "2022-02-28"
EXCLUDED_DATE_END = "2022-03-23"

# If multiple board rows exist for one ticker/date:
# VALUE and VOLUME are summed across approved boards.
# OHLC is taken from TQBR when available, otherwise EQBR.
PRICE_BOARD_PRIORITY = {"TQBR": 0, "EQBR": 1}

# Increment this when the construction logic changes.
PIPELINE_VERSION = "1.0.0"


# ============================================================
# UTILITIES
# ============================================================

def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def ensure_directories() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def get_source_fingerprint() -> dict:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    stat = DB_PATH.stat()

    return {
        "database_path": str(DB_PATH.resolve()),
        "database_size_bytes": stat.st_size,
        "database_mtime_ns": stat.st_mtime_ns,
    }


def get_config_fingerprint() -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "analysis_start_date": ANALYSIS_START_DATE,
        "top_n": TOP_N,
        "lookback_sessions": LOOKBACK_SESSIONS,
        "approved_boards": list(APPROVED_BOARDS),
        "excluded_tickers": sorted(EXCLUDED_TICKERS),
        "excluded_date_start": EXCLUDED_DATE_START,
        "excluded_date_end": EXCLUDED_DATE_END,
        "price_board_priority": PRICE_BOARD_PRIORITY,
        "table_name": TABLE_NAME,
    }


def cache_paths() -> dict[str, Path]:
    return {
        "membership": OUTPUT_DIR / "universe_membership.parquet",
        "panel": OUTPUT_DIR / "universe_panel.parquet",
        "metadata": OUTPUT_DIR / "metadata.json",
    }


def load_metadata() -> dict | None:
    path = cache_paths()["metadata"]

    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def cache_is_valid() -> bool:
    paths = cache_paths()

    if not all(path.exists() for path in paths.values()):
        return False

    metadata = load_metadata()

    if metadata is None:
        return False

    return (
        metadata.get("source") == get_source_fingerprint()
        and metadata.get("config") == get_config_fingerprint()
    )


def atomic_write_parquet(
    dataframe: pd.DataFrame,
    destination: Path,
) -> None:
    temporary = destination.with_name(
        destination.stem + ".tmp" + destination.suffix
    )

    dataframe.to_parquet(
        temporary,
        index=False,
        compression="zstd",
    )

    temporary.replace(destination)


def atomic_write_json(data: dict, destination: Path) -> None:
    temporary = destination.with_name(destination.name + ".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    temporary.replace(destination)


# ============================================================
# LOAD SOURCE DATA
# ============================================================

def load_source_data() -> pd.DataFrame:
    log("Loading source data from SQLite...")

    placeholders = ",".join("?" for _ in APPROVED_BOARDS)

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
        FROM {TABLE_NAME}
        WHERE tradedate >= ?
          AND boardid IN ({placeholders})
          AND secid NOT IN ({",".join("?" for _ in EXCLUDED_TICKERS)})
        ORDER BY tradedate, secid, boardid
    """

    params = [
        ANALYSIS_START_DATE,
        *APPROVED_BOARDS,
        *sorted(EXCLUDED_TICKERS),
    ]

    with sqlite3.connect(DB_PATH) as connection:
        dataframe = pd.read_sql_query(
            query,
            connection,
            params=params,
        )

    if dataframe.empty:
        raise RuntimeError("Source query returned no rows.")

    dataframe["tradedate"] = pd.to_datetime(
        dataframe["tradedate"],
        errors="coerce",
    )

    dataframe["secid"] = dataframe["secid"].astype(str)
    dataframe["boardid"] = dataframe["boardid"].astype(str)

    numeric_columns = [
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "adjusted_volume",
        "value",
    ]

    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )

    dataframe = dataframe.dropna(
        subset=["secid", "tradedate", "boardid"]
    )

    # Exclude the known trading suspension period.
    suspension_mask = (
        (dataframe["tradedate"] >= EXCLUDED_DATE_START)
        & (dataframe["tradedate"] <= EXCLUDED_DATE_END)
    )

    removed_rows = int(suspension_mask.sum())

    dataframe = dataframe.loc[~suspension_mask].copy()

    log(f"Rows loaded: {len(dataframe):,}")
    log(f"Rows removed for suspension period: {removed_rows:,}")

    return dataframe


# ============================================================
# COLLAPSE MULTIPLE BOARDS
# ============================================================

def aggregate_board_rows(
    source: pd.DataFrame,
) -> pd.DataFrame:
    log("Aggregating approved boards...")

    keys = ["secid", "tradedate"]

    # VALUE and volume are aggregated across approved boards.
    aggregated_activity = (
        source.groupby(keys, as_index=False)
        .agg(
            volume=("adjusted_volume", "sum"),
            value=("value", "sum"),
            n_boards=("boardid", "nunique"),
            boards=(
                "boardid",
                lambda values: "|".join(sorted(set(values))),
            ),
        )
    )

    # OHLC cannot be summed.
    # Select one deterministic price source:
    # TQBR first, EQBR as fallback.
    prices = source.copy()

    prices["_board_priority"] = (
        prices["boardid"]
        .map(PRICE_BOARD_PRIORITY)
        .fillna(999)
    )

    prices = (
        prices.sort_values(
            keys + ["_board_priority", "boardid"]
        )
        .drop_duplicates(keys, keep="first")
    )

    price_columns = [
        "secid",
        "tradedate",
        "adjusted_open",
        "adjusted_high",
        "adjusted_low",
        "adjusted_close",
        "boardid",
    ]

    prices = prices[price_columns].rename(
        columns={"boardid": "price_board"}
    )

    result = aggregated_activity.merge(
        prices,
        on=keys,
        how="left",
        validate="one_to_one",
    )

    if result.duplicated(keys).any():
        raise RuntimeError(
            "Duplicate SECID/date rows remain after aggregation."
        )

    if result["value"].isna().any():
        raise RuntimeError(
            "NULL VALUE encountered after board aggregation."
        )

    if (result["value"] < 0).any():
        raise RuntimeError(
            "Negative VALUE encountered after board aggregation."
        )

    log(f"Aggregated SECID/date rows: {len(result):,}")
    log(f"Tickers: {result['secid'].nunique():,}")
    log(
        "Multi-board groups: "
        f"{int((result['n_boards'] > 1).sum()):,}"
    )

    return result


# ============================================================
# BUILD MARKET CALENDAR AND ROLLING VALUE
# ============================================================

def build_market_calendar(
    daily: pd.DataFrame,
) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(
        sorted(daily["tradedate"].dropna().unique())
    )

    if len(dates) == 0:
        raise RuntimeError("Market calendar is empty.")

    return dates


def build_rolling_value_matrix(
    daily: pd.DataFrame,
    market_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    log("Building ticker x date VALUE matrix...")

    value_matrix = daily.pivot(
        index="tradedate",
        columns="secid",
        values="value",
    )

    value_matrix = value_matrix.reindex(
        index=market_dates
    )

    # Missing observations inside a ticker's observed lifetime
    # represent zero recorded turnover for that session.
    #
    # Dates before the ticker's first observation remain NaN.
    value_matrix = value_matrix.sort_index()

    first_dates = daily.groupby("secid")["tradedate"].min()

    for ticker in value_matrix.columns:
        first_date = first_dates[ticker]

        value_matrix.loc[
            value_matrix.index < first_date,
            ticker,
        ] = np.nan

    # For rolling calculations, missing observations after listing
    # are treated as zero turnover.
    value_for_rolling = value_matrix.fillna(0.0)

    rolling_value = value_for_rolling.rolling(
        window=LOOKBACK_SESSIONS,
        min_periods=LOOKBACK_SESSIONS,
    ).sum()

    # Prevent a ticker from becoming eligible before it has existed
    # for a complete 60-session window.
    #
    # The first 59 global market sessions therefore form warm-up.
    # A newly listed ticker also needs 60 market sessions.
    observed_history = (
        value_matrix.notna()
        .astype("int16")
        .rolling(
            window=LOOKBACK_SESSIONS,
            min_periods=LOOKBACK_SESSIONS,
        )
        .sum()
    )

    full_history = observed_history >= LOOKBACK_SESSIONS

    eligible_value = rolling_value.where(full_history)

    return value_matrix, eligible_value


# ============================================================
# BUILD DAILY MEMBERSHIP
# ============================================================

def build_membership(
    eligible_value: pd.DataFrame,
) -> pd.DataFrame:
    log("Selecting daily universe membership...")

    records = []

    for date, row in eligible_value.iterrows():
        candidates = row.dropna()
        candidates = candidates[candidates > 0]

        if candidates.empty:
            continue

        candidates = candidates.sort_values(
            ascending=False,
            kind="mergesort",
        )

        # If fewer than TOP_N tickers are eligible,
        # include every eligible ticker.
        selected = candidates.head(TOP_N)

        universe_size = len(selected)

        if universe_size < TOP_N:
            selection_method = "ALL_AVAILABLE"
        else:
            selection_method = f"TOP{TOP_N}_VALUE_{LOOKBACK_SESSIONS}D"

        for rank, (ticker, value_60d) in enumerate(
            selected.items(),
            start=1,
        ):
            records.append(
                {
                    "tradedate": date,
                    "secid": ticker,
                    "value_60d": float(value_60d),
                    "rank": rank,
                    "universe_size": universe_size,
                    "selection_method": selection_method,
                }
            )

    membership = pd.DataFrame.from_records(records)

    if membership.empty:
        raise RuntimeError(
            "Universe membership is empty. "
            "Check source data and rolling window settings."
        )

    membership["tradedate"] = pd.to_datetime(
        membership["tradedate"]
    )

    membership = membership.sort_values(
        ["tradedate", "rank", "secid"]
    ).reset_index(drop=True)

    if membership.duplicated(["tradedate", "secid"]).any():
        raise RuntimeError(
            "Duplicate ticker membership for a date."
        )

    daily_sizes = membership.groupby("tradedate")["secid"].nunique()

    if (daily_sizes > TOP_N).any():
        raise RuntimeError("TOP_N limit was exceeded.")

    log(f"Membership rows: {len(membership):,}")
    log(f"Dates with a non-empty universe: {len(daily_sizes):,}")

    return membership


# ============================================================
# BUILD FILTERED PANEL
# ============================================================

def build_universe_panel(
    membership: pd.DataFrame,
    daily: pd.DataFrame,
) -> pd.DataFrame:
    log("Building filtered historical panel...")

    panel = membership.merge(
        daily,
        on=["tradedate", "secid"],
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    # Membership is based on rolling historical VALUE.
    # A ticker can remain in the universe on a date with no
    # current-day bar, if it had turnover earlier in the window.
    #
    # Such a missing current observation is kept explicit.
    panel["bar_available"] = (
        panel["_merge"] == "both"
    ).astype("int8")

    panel = panel.drop(columns=["_merge"])

    # No OHLC forward fill.
    # Missing prices remain NaN.
    #
    # Missing current-day turnover is zero for the selected ticker.
    panel["volume"] = panel["volume"].fillna(0.0)
    panel["value"] = panel["value"].fillna(0.0)

    panel = panel.sort_values(
        ["tradedate", "rank", "secid"]
    ).reset_index(drop=True)

    if panel.duplicated(["tradedate", "secid"]).any():
        raise RuntimeError(
            "Duplicate rows in final universe panel."
        )

    if not (panel["universe_size"] <= TOP_N).all():
        raise RuntimeError(
            "Panel contains dates with more than TOP_N tickers."
        )

    log(f"Panel rows: {len(panel):,}")
    log(f"Panel tickers: {panel['secid'].nunique():,}")
    log(
        "Rows without current-day bar: "
        f"{int((panel['bar_available'] == 0).sum()):,}"
    )

    return panel


# ============================================================
# REPORTS
# ============================================================

def build_summary(
    membership: pd.DataFrame,
    panel: pd.DataFrame,
    market_dates: pd.DatetimeIndex,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sizes = (
        membership.groupby("tradedate")
        .agg(
            universe_size=("secid", "nunique"),
            min_value_60d=("value_60d", "min"),
            max_value_60d=("value_60d", "max"),
        )
        .reindex(market_dates)
    )

    sizes["universe_size"] = (
        sizes["universe_size"].fillna(0).astype(int)
    )

    sizes["selection_method"] = np.where(
        sizes["universe_size"] == 0,
        "NO_ELIGIBLE_TICKERS",
        np.where(
            sizes["universe_size"] < TOP_N,
            "ALL_AVAILABLE",
            f"TOP{TOP_N}_VALUE_{LOOKBACK_SESSIONS}D",
        ),
    )

    sizes.index.name = "tradedate"
    daily_summary = sizes.reset_index()

    summary = pd.DataFrame(
        [
            {
                "metric": "market_dates",
                "value": len(market_dates),
            },
            {
                "metric": "dates_with_universe",
                "value": int((daily_summary["universe_size"] > 0).sum()),
            },
            {
                "metric": "dates_with_top_n",
                "value": int((daily_summary["universe_size"] >= TOP_N).sum()),
            },
            {
                "metric": "dates_below_top_n",
                "value": int((daily_summary["universe_size"] < TOP_N).sum()),
            },
            {
                "metric": "min_universe_size",
                "value": int(daily_summary["universe_size"].min()),
            },
            {
                "metric": "max_universe_size",
                "value": int(daily_summary["universe_size"].max()),
            },
            {
                "metric": "panel_rows",
                "value": len(panel),
            },
            {
                "metric": "panel_tickers",
                "value": panel["secid"].nunique(),
            },
            {
                "metric": "panel_start_date",
                "value": str(panel["tradedate"].min().date()),
            },
            {
                "metric": "panel_end_date",
                "value": str(panel["tradedate"].max().date()),
            },
            {
                "metric": "rows_without_bar",
                "value": int((panel["bar_available"] == 0).sum()),
            },
        ]
    )

    return daily_summary, summary


def write_report(
    daily_summary: pd.DataFrame,
    summary: pd.DataFrame,
    membership: pd.DataFrame,
    panel: pd.DataFrame,
) -> None:
    summary_path = REPORT_DIR / "summary.csv"
    daily_path = REPORT_DIR / "daily_universe_summary.csv"
    report_path = REPORT_DIR / "03_HISTORICAL_UNIVERSE.md"

    summary.to_csv(summary_path, index=False)
    daily_summary.to_csv(daily_path, index=False)

    method_counts = (
        daily_summary["selection_method"]
        .value_counts()
        .to_dict()
    )

    text = f"""# Historical Universe

## Configuration

- Analysis start: `{ANALYSIS_START_DATE}`
- Selection: TOP-{TOP_N} by trailing {LOOKBACK_SESSIONS}-session VALUE
- Boards: `{", ".join(APPROVED_BOARDS)}`
- Excluded tickers: `{", ".join(sorted(EXCLUDED_TICKERS))}`
- Excluded suspension: `{EXCLUDED_DATE_START}` to `{EXCLUDED_DATE_END}`
- Pipeline version: `{PIPELINE_VERSION}`

## Construction rules

1. VALUE is summed across approved boards for each ticker/date.
2. OHLC is taken from TQBR when available, otherwise EQBR.
3. Missing turnover inside the observed ticker lifetime is treated as zero.
4. Dates before a ticker's first observation are not treated as zero.
5. A ticker needs a complete 60-session history to qualify.
6. Ranking uses only trailing VALUE available on the selection date.
7. If fewer than {TOP_N} tickers qualify, all eligible tickers are selected.
8. No forward fill is applied to OHLC.
9. Missing current-day bars remain explicit in the panel.

## Results

- Market dates: {len(daily_summary):,}
- Membership rows: {len(membership):,}
- Panel rows: {len(panel):,}
- Panel tickers: {panel["secid"].nunique():,}
- Panel date range: {panel["tradedate"].min().date()} to {panel["tradedate"].max().date()}

### Selection methods

{json.dumps(method_counts, ensure_ascii=False, indent=2)}

## Output files

- `data/derived/historical_universe/universe_membership.parquet`
- `data/derived/historical_universe/universe_panel.parquet`
- `data/derived/historical_universe/metadata.json`
- `reports/eda/historical_universe/summary.csv`
- `reports/eda/historical_universe/daily_universe_summary.csv`

## Important limitations

- Trailing VALUE is a historical turnover ranking, not a direct measure of order-book depth,
  spread, market impact, or executable capacity.
- The current-day universe is known only after that day's closing data is available.
  For next-session trading, use the membership calculated at the previous close.
- OHLC is sourced from one preferred board, while VALUE and volume are summed across
  approved boards. This is a deliberate aggregation convention and should remain documented.
- The panel is a filtered research dataset, not a claim that every selected ticker
  was tradable at every point during every session.
"""

    report_path.write_text(text, encoding="utf-8")

    log(f"Report written: {report_path}")


# ============================================================
# MAIN
# ============================================================

def build(force: bool = False) -> None:
    ensure_directories()

    paths = cache_paths()

    if not force and cache_is_valid():
        log("VALID CACHE FOUND")
        log(f"Membership: {paths['membership']}")
        log(f"Panel: {paths['panel']}")
        log("Nothing to rebuild. Use --force to rebuild.")
        return

    log("Cache missing or outdated. Starting build.")

    source = load_source_data()
    daily = aggregate_board_rows(source)

    market_dates = build_market_calendar(daily)

    log(f"Market dates: {len(market_dates):,}")
    log(f"First market date: {market_dates.min().date()}")
    log(f"Last market date: {market_dates.max().date()}")

    _, eligible_value = build_rolling_value_matrix(
        daily,
        market_dates,
    )

    membership = build_membership(eligible_value)

    panel = build_universe_panel(
        membership,
        daily,
    )

    daily_summary, summary = build_summary(
        membership,
        panel,
        market_dates,
    )

    log("Writing Parquet cache...")

    atomic_write_parquet(
        membership,
        paths["membership"],
    )

    atomic_write_parquet(
        panel,
        paths["panel"],
    )

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": get_source_fingerprint(),
        "config": get_config_fingerprint(),
        "outputs": {
            "membership_rows": len(membership),
            "panel_rows": len(panel),
            "panel_tickers": int(panel["secid"].nunique()),
            "market_dates": len(market_dates),
            "membership_path": str(paths["membership"]),
            "panel_path": str(paths["panel"]),
        },
    }

    atomic_write_json(
        metadata,
        paths["metadata"],
    )

    write_report(
        daily_summary,
        summary,
        membership,
        panel,
    )

    log("=" * 60)
    log("BUILD COMPLETE")
    log("=" * 60)

    print(summary.to_string(index=False))

    log("")
    log("Cached datasets:")
    log(f"  {paths['membership']}")
    log(f"  {paths['panel']}")
    log(f"  {paths['metadata']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build point-in-time historical TOP-N universe."
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force full rebuild even if cache is valid.",
    )

    args = parser.parse_args()

    try:
        build(force=args.force)
    except Exception as error:
        log(f"ERROR: {error}")
        raise


if __name__ == "__main__":
    main()
