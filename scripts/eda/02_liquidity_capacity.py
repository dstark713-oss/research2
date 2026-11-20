
# research2/scripts/eda/02_liquidity_capacity.py
"""
Historical liquidity-capacity diagnostic for a TOP-N universe selected by VALUE.

VALUE is measured in RUB.
TQBR and EQBR are treated as separate approved trading boards.
Their daily turnover is summed at SECID + TRADEDATE level.

The 2022-02-28 through 2022-03-23 trading pause is excluded.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


# =============================================================================
# CONFIG
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "market_data"
    / "moex_daily_adjusted.sqlite"
)

TABLE_NAME = "adjusted_daily_bars"

ANALYSIS_START_DATE = "2005-01-01"

APPROVED_BOARDS = ("TQBR", "EQBR")
EXCLUDED_SECIDS = ("GAZC", "GAZS", "GAZT")

EXCLUDED_PERIODS = (
    ("2022-02-28", "2022-03-23"),
)

LOOKBACK_SESSIONS = 60
TOP_N_VALUES = (50, 75, 100)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "reports"
    / "eda"
    / "liquidity_capacity"
)


# =============================================================================
# DATA LOADING
# =============================================================================

def load_daily_value() -> tuple[pd.DataFrame, dict]:

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    boards_sql = ", ".join(
        f"'{board}'" for board in APPROVED_BOARDS
    )

    excluded_sql = ", ".join(
        f"'{secid}'" for secid in EXCLUDED_SECIDS
    )

    query = f"""
        SELECT
            secid,
            tradedate,
            boardid,
            value
        FROM {TABLE_NAME}
        WHERE tradedate >= ?
          AND boardid IN ({boards_sql})
          AND secid NOT IN ({excluded_sql})
    """

    with sqlite3.connect(DB_PATH) as conn:
        raw = pd.read_sql_query(
            query,
            conn,
            params=(ANALYSIS_START_DATE,),
        )

    if raw.empty:
        raise RuntimeError("No observations returned from database.")

    raw["tradedate"] = pd.to_datetime(
        raw["tradedate"],
        errors="coerce",
    )

    if raw["tradedate"].isna().any():
        raise RuntimeError("Invalid TRADEDATE values found.")

    # Exclude the 2022 trading pause.
    for start, end in EXCLUDED_PERIODS:
        raw = raw.loc[
            ~raw["tradedate"].between(
                pd.Timestamp(start),
                pd.Timestamp(end),
            )
        ].copy()

    if raw.empty:
        raise RuntimeError("No observations remain after date filters.")

    if raw["value"].isna().any():
        sample = raw.loc[
            raw["value"].isna()
        ].head(10)

        raise RuntimeError(
            "NULL VALUE observations found:\n"
            f"{sample.to_string(index=False)}"
        )

    if (raw["value"] < 0).any():
        sample = raw.loc[
            raw["value"] < 0
        ].head(10)

        raise RuntimeError(
            "Negative VALUE observations found:\n"
            f"{sample.to_string(index=False)}"
        )

    # A duplicate within the same board is an error.
    # Different boards are separate turnover records.
    duplicates = raw.duplicated(
        subset=["secid", "tradedate", "boardid"],
        keep=False,
    )

    if duplicates.any():
        sample = raw.loc[
            duplicates,
            ["secid", "tradedate", "boardid", "value"],
        ].head(30)

        raise RuntimeError(
            "Duplicate SECID + TRADEDATE + BOARDID records:\n"
            f"{sample.to_string(index=False)}"
        )

    raw_rows = len(raw)

    unique_security_dates = (
        raw[["secid", "tradedate"]]
        .drop_duplicates()
        .shape[0]
    )

    # Aggregate VALUE across approved boards.
    daily = (
        raw.groupby(
            ["secid", "tradedate"],
            as_index=False,
        )
        .agg(
            value=("value", "sum"),
            n_boards=("boardid", "nunique"),
            boards=(
                "boardid",
                lambda x: ",".join(sorted(set(x))),
            ),
        )
    )

    if daily.duplicated(
        ["secid", "tradedate"]
    ).any():
        raise RuntimeError(
            "Aggregation did not produce unique SECID + TRADEDATE."
        )

    audit = {
        "raw_rows_after_filters": raw_rows,
        "unique_security_date_groups": unique_security_dates,
        "aggregated_security_date_rows": len(daily),
        "multi_board_groups": int(
            (daily["n_boards"] > 1).sum()
        ),
        "boards_included": ",".join(APPROVED_BOARDS),
        "excluded_secids": ",".join(EXCLUDED_SECIDS),
    }

    return daily, audit


# =============================================================================
# PANEL CONSTRUCTION
# =============================================================================

def build_rolling_value_panel(
    daily: pd.DataFrame,
) -> pd.DataFrame:

    market_dates = pd.DatetimeIndex(
        sorted(daily["tradedate"].unique()),
        name="tradedate",
    )

    tickers = sorted(
        daily["secid"].unique().tolist()
    )

    panel = (
        daily.pivot(
            index="tradedate",
            columns="secid",
            values="value",
        )
        .reindex(
            index=market_dates,
            columns=tickers,
        )
        .astype(float)
    )

    # Missing observations are zero only inside the ticker's
    # observed lifetime. No forward fill is used.
    for secid in tickers:

        observed = panel[secid].dropna()

        if observed.empty:
            continue

        first_date = observed.index[0]
        last_date = observed.index[-1]

        panel.loc[
            first_date:last_date,
            secid,
        ] = panel.loc[
            first_date:last_date,
            secid,
        ].fillna(0.0)

    rolling_value = panel.rolling(
        window=LOOKBACK_SESSIONS,
        min_periods=LOOKBACK_SESSIONS,
    ).sum()

    return rolling_value


# =============================================================================
# TOP-N CAPACITY
# =============================================================================

def calculate_cutoff(
    row: pd.Series,
    n: int,
) -> float:

    eligible = row.dropna()
    eligible = eligible[eligible > 0]

    if len(eligible) < n:
        return float("nan")

    return float(
        eligible.nlargest(n).iloc[-1]
    )


def build_daily_capacity(
    rolling_value: pd.DataFrame,
) -> pd.DataFrame:

    eligible_mask = (
        rolling_value.notna()
        & rolling_value.gt(0)
    )

    eligible_count = eligible_mask.sum(axis=1)

    result = pd.DataFrame(
        {
            "tradedate": rolling_value.index,
            "eligible_tickers": eligible_count.values,
        }
    )

    for n in TOP_N_VALUES:

        result[f"top{n}_available"] = (
            result["eligible_tickers"] >= n
        )

        result[f"top{n}_cutoff_value_rub"] = [
            calculate_cutoff(row, n)
            for _, row in rolling_value.iterrows()
        ]

    return result


# =============================================================================
# SHORTAGE PERIODS
# =============================================================================

def build_shortage_periods(
    daily_capacity: pd.DataFrame,
    target_n: int = 50,
) -> pd.DataFrame:

    shortage = (
        daily_capacity["eligible_tickers"] < target_n
    )

    columns = [
        "start_date",
        "end_date",
        "market_sessions",
        "min_eligible_tickers",
        "max_eligible_tickers",
    ]

    if not shortage.any():
        return pd.DataFrame(columns=columns)

    group_id = (
        shortage.ne(
            shortage.shift(fill_value=False)
        ).cumsum()
    )

    subset = daily_capacity.loc[shortage].copy()

    subset["group_id"] = group_id.loc[
        shortage
    ].values

    rows = []

    for _, group in subset.groupby("group_id"):

        rows.append(
            {
                "start_date": (
                    group["tradedate"]
                    .min()
                    .date()
                    .isoformat()
                ),
                "end_date": (
                    group["tradedate"]
                    .max()
                    .date()
                    .isoformat()
                ),
                "market_sessions": len(group),
                "min_eligible_tickers": int(
                    group["eligible_tickers"].min()
                ),
                "max_eligible_tickers": int(
                    group["eligible_tickers"].max()
                ),
            }
        )

    return pd.DataFrame(rows, columns=columns)


# =============================================================================
# SUMMARY
# =============================================================================

def build_summary(
    daily: pd.DataFrame,
    daily_capacity: pd.DataFrame,
    audit: dict,
) -> pd.DataFrame:

    counts = daily_capacity["eligible_tickers"]

    enough_dates = int((counts >= 50).sum())
    shortage_dates = int((counts < 50).sum())

    min_idx = counts.idxmin()
    max_idx = counts.idxmax()

    summary = {
        **audit,

        "analysis_start_date": ANALYSIS_START_DATE,

        "first_market_date": (
            daily_capacity["tradedate"]
            .min()
            .date()
            .isoformat()
        ),

        "last_market_date": (
            daily_capacity["tradedate"]
            .max()
            .date()
            .isoformat()
        ),

        "market_dates": len(daily_capacity),
        "tickers": int(daily["secid"].nunique()),

        "lookback_sessions": LOOKBACK_SESSIONS,

        "dates_with_at_least_50_eligible": enough_dates,
        "dates_with_fewer_than_50_eligible": shortage_dates,

        "share_dates_with_at_least_50_pct": (
            100 * enough_dates / len(daily_capacity)
        ),

        "minimum_eligible_tickers": int(counts.min()),

        "minimum_count_date": (
            daily_capacity.loc[
                min_idx,
                "tradedate",
            ].date().isoformat()
        ),

        "maximum_eligible_tickers": int(counts.max()),

        "maximum_count_date": (
            daily_capacity.loc[
                max_idx,
                "tradedate",
            ].date().isoformat()
        ),
    }

    return pd.DataFrame([summary])


# =============================================================================
# REPORT
# =============================================================================

def write_report(
    daily_capacity: pd.DataFrame,
    shortage_periods: pd.DataFrame,
    summary: pd.DataFrame,
    audit: dict,
) -> Path:

    s = summary.iloc[0]

    lines = [
        "# Liquidity Capacity Diagnostic",
        "",
        "## Research question",
        "",
        (
            f"Does the historical universe contain at least 50 securities "
            f"with positive trailing {LOOKBACK_SESSIONS}-session VALUE "
            f"on each market date?"
        ),
        "",
        "## Methodology",
        "",
        f"- Source: `{DB_PATH.relative_to(PROJECT_ROOT)}`",
        f"- Table: `{TABLE_NAME}`",
        f"- Analysis start: `{ANALYSIS_START_DATE}`",
        f"- Boards included: `{', '.join(APPROVED_BOARDS)}`",
        f"- Excluded securities: `{', '.join(EXCLUDED_SECIDS)}`",
        "- VALUE is measured in RUB.",
        "- Board-level VALUE is summed for each SECID/date.",
        "- Missing observations are zero only inside observed ticker lifetime.",
        "- No forward fill is applied.",
        (
            f"- Rolling VALUE uses {LOOKBACK_SESSIONS} market sessions, "
            "including the current session."
        ),
        "- A ticker is eligible only if its full rolling window exists and VALUE is positive.",
        "- The 2022-02-28 through 2022-03-23 pause is excluded.",
        "",
        "## Results",
        "",
        f"- Market dates: {int(s['market_dates']):,}",
        f"- Tickers: {int(s['tickers']):,}",
        f"- Dates with at least 50 eligible tickers: {int(s['dates_with_at_least_50_eligible']):,}",
        f"- Dates below TOP-50 capacity: {int(s['dates_with_fewer_than_50_eligible']):,}",
        f"- Share of dates with TOP-50 capacity: {s['share_dates_with_at_least_50_pct']:.2f}%",
        f"- Minimum eligible count: {int(s['minimum_eligible_tickers'])} on {s['minimum_count_date']}",
        f"- Maximum eligible count: {int(s['maximum_eligible_tickers'])} on {s['maximum_count_date']}",
        "",
        "## Board aggregation audit",
        "",
        f"- Raw rows after filters: {int(audit['raw_rows_after_filters']):,}",
        f"- SECID/date groups before aggregation: {int(audit['unique_security_date_groups']):,}",
        f"- Multi-board SECID/date groups: {int(audit['multi_board_groups']):,}",
        "",
        "## Shortage periods",
        "",
    ]

    if shortage_periods.empty:

        lines.append(
            "No shortage periods: at least 50 eligible tickers "
            "were available on every date."
        )

    else:

        lines.extend(
            [
                "| Start | End | Sessions | Min eligible | Max eligible |",
                "|---|---|---:|---:|---:|",
            ]
        )

        for row in shortage_periods.itertuples(index=False):

            lines.append(
                f"| {row.start_date} | {row.end_date} "
                f"| {row.market_sessions} "
                f"| {row.min_eligible_tickers} "
                f"| {row.max_eligible_tickers} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- This is a historical universe-capacity diagnostic, not proof of executable capacity.",
            "- VALUE is a turnover proxy. It does not measure order-book depth, spread, market impact, or participation limits.",
            "- Summing approved boards estimates turnover across the selected board set. A strategy restricted to one board requires a separate test.",
            "- No inflation adjustment is applied because ranking is cross-sectional within each date.",
            "",
            "## Output files",
            "",
            "- `daily_capacity.csv`",
            "- `shortage_periods.csv`",
            "- `summary.csv`",
            "",
        ]
    )

    report_path = (
        OUTPUT_DIR / "02_LIQUIDITY_CAPACITY.md"
    )

    report_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return report_path


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 70)
    print("LIQUIDITY CAPACITY DIAGNOSTIC")
    print("=" * 70)

    daily, audit = load_daily_value()

    print(
        f"Aggregated SECID/date rows: {len(daily):,}"
    )

    print(
        f"Tickers: {daily['secid'].nunique():,}"
    )

    print(
        f"Multi-board groups: {audit['multi_board_groups']:,}"
    )

    rolling_value = build_rolling_value_panel(daily)

    daily_capacity = build_daily_capacity(
        rolling_value
    )

    shortage_periods = build_shortage_periods(
        daily_capacity,
        target_n=50,
    )

    summary = build_summary(
        daily,
        daily_capacity,
        audit,
    )

    daily_capacity.to_csv(
        OUTPUT_DIR / "daily_capacity.csv",
        index=False,
        date_format="%Y-%m-%d",
    )

    shortage_periods.to_csv(
        OUTPUT_DIR / "shortage_periods.csv",
        index=False,
    )

    summary.to_csv(
        OUTPUT_DIR / "summary.csv",
        index=False,
    )

    report_path = write_report(
        daily_capacity,
        shortage_periods,
        summary,
        audit,
    )

    s = summary.iloc[0]

    print("\nRESULT")

    print(
        f"Market dates: {int(s['market_dates']):,}"
    )

    print(
        f"Tickers: {int(s['tickers']):,}"
    )

    print(
        "Dates with TOP-50 capacity: "
        f"{int(s['dates_with_at_least_50_eligible']):,} "
        f"({s['share_dates_with_at_least_50_pct']:.2f}%)"
    )

    print(
        "Dates below TOP-50 capacity: "
        f"{int(s['dates_with_fewer_than_50_eligible']):,}"
    )

    print(
        f"Minimum eligible tickers: "
        f"{int(s['minimum_eligible_tickers'])}"
    )

    print(
        f"Maximum eligible tickers: "
        f"{int(s['maximum_eligible_tickers'])}"
    )

    print("\nOUTPUT")

    print(f"Directory: {OUTPUT_DIR}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
