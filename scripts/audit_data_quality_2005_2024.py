
# Path: scripts/audit_data_quality_2005_2024.py
# Description: Read-only forensic data quality audit of MOEX daily OHLCV data, 2005-2024.

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path


START_DATE = "2005-01-01"
END_DATE = "2024-12-31"

REQUIRED_COLUMNS = {
    "secid", "boardid", "tradedate", "numtrades",
    "volume", "value", "open", "high", "low", "close",
}

PRICE_FIELDS = ("open", "high", "low", "close")
COMPARE_FIELDS = (
    "tradingsession", "trade_session_date",
    "numtrades", "value", "volume",
    "open", "low", "high", "legalcloseprice",
    "waprice", "close", "marketprice2", "marketprice3",
    "admittedquote", "admittedvalue",
)

# These are investigation thresholds, not automatic exclusion rules.
SUSPICIOUS_RETURN_THRESHOLD = 0.50
GAP_MIN_MARKET_DATES = 5
NUMERIC_TOLERANCE = 1e-9


def parse_args():
    parser = argparse.ArgumentParser(
        description="Read-only MOEX OHLCV data quality audit, 2005-2024."
    )
    parser.add_argument(
        "--db",
        default="data/market_data/moex_daily_raw.sqlite",
        help="Path to raw SQLite database.",
    )
    parser.add_argument(
        "--output",
        default="data/reports/data_quality_2005_2024",
        help="Directory for audit reports.",
    )
    return parser.parse_args()


def open_readonly_database(db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def inspect_schema(connection):
    rows = connection.execute(
        "PRAGMA table_info(daily_bars_raw)"
    ).fetchall()

    if not rows:
        raise RuntimeError("Table daily_bars_raw not found.")

    actual = {row["name"].lower(): row["name"] for row in rows}
    missing = REQUIRED_COLUMNS - set(actual)

    if missing:
        raise RuntimeError(
            f"Missing required columns: {sorted(missing)}"
        )

    return actual


def write_csv(path: Path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def is_missing(value):
    return value is None


def is_finite_number(value):
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def numeric_equal(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return a == b

    if not math.isfinite(a) or not math.isfinite(b):
        return a == b

    return math.isclose(
        a, b,
        rel_tol=NUMERIC_TOLERANCE,
        abs_tol=NUMERIC_TOLERANCE,
    )


def add_flag(flags, row, flag_type, severity, reason):
    flags.append({
        "secid": row["secid"],
        "boardid": row["boardid"],
        "tradedate": row["tradedate"],
        "flag_type": flag_type,
        "severity": severity,
        "reason": reason,
        "numtrades": row["numtrades"],
        "volume": row["volume"],
        "value": row["value"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
    })


def audit_row(row, flags):
    trades = row["numtrades"]
    volume = row["volume"]
    value = row["value"]

    prices = {field: row[field] for field in PRICE_FIELDS}

    if trades is not None and trades < 0:
        add_flag(
            flags, row, "negative_numtrades", "critical",
            "NUMTRADES is negative.",
        )

    if volume is not None and volume < 0:
        add_flag(
            flags, row, "negative_volume", "critical",
            "VOLUME is negative.",
        )

    if value is not None and value < 0:
        add_flag(
            flags, row, "negative_value", "critical",
            "VALUE is negative.",
        )

    if trades == 0:
        add_flag(
            flags, row, "zero_trades", "info",
            "No trades reported for this security, board and date.",
        )

    if volume == 0:
        add_flag(
            flags, row, "zero_volume", "info",
            "Zero reported volume.",
        )

    if trades is not None and trades > 0:
        missing_prices = [
            field for field, price in prices.items()
            if price is None
        ]
        zero_prices = [
            field for field, price in prices.items()
            if price == 0
        ]

        if missing_prices:
            add_flag(
                flags, row, "traded_with_missing_ohlc", "high",
                "Positive trades but missing OHLC: "
                + ",".join(missing_prices),
            )

        if zero_prices:
            add_flag(
                flags, row, "traded_with_zero_ohlc", "high",
                "Positive trades but zero OHLC: "
                + ",".join(zero_prices),
            )

    if trades == 0 and all(price == 0 for price in prices.values()):
        add_flag(
            flags, row, "no_trade_all_ohlc_zero", "info",
            "No trades and all four OHLC values are zero.",
        )

    if any(price == 0 for price in prices.values()):
        add_flag(
            flags, row, "literal_zero_ohlc", "warning",
            "At least one OHLC field contains literal zero.",
        )

    if any(price is None for price in prices.values()):
        add_flag(
            flags, row, "missing_ohlc", "warning",
            "At least one OHLC field is NULL.",
        )

    finite = {
        field: float(price)
        for field, price in prices.items()
        if is_finite_number(price)
    }

    if len(finite) == 4:
        o = finite["open"]
        h = finite["high"]
        l = finite["low"]
        c = finite["close"]

        if h < l:
            add_flag(
                flags, row, "high_below_low", "critical",
                "HIGH is below LOW.",
            )

        if h < max(o, c):
            add_flag(
                flags, row, "high_below_open_or_close", "high",
                "HIGH is below OPEN or CLOSE.",
            )

        if l > min(o, c):
            add_flag(
                flags, row, "low_above_open_or_close", "high",
                "LOW is above OPEN or CLOSE.",
            )

    if trades is not None and trades > 0:
        if row["close"] == 0:
            add_flag(
                flags, row, "positive_trades_zero_close", "critical",
                "Positive trades but CLOSE equals zero.",
            )


def fetch_rows(connection, columns):
    selected = [
        "secid", "boardid", "tradedate",
        "tradingsession", "trade_session_date",
        "numtrades", "value", "volume",
        "open", "low", "high", "legalcloseprice",
        "waprice", "close", "marketprice2",
        "marketprice3", "admittedquote", "admittedvalue",
    ]

    actual = {
        name: columns[name]
        for name in selected
        if name in columns
    }

    select_sql = ", ".join(
        f'"{actual[name]}" AS "{name}"'
        for name in actual
    )

    sql = f"""
        SELECT {select_sql}
        FROM daily_bars_raw
        WHERE tradedate >= ?
          AND tradedate <= ?
        ORDER BY secid, boardid, tradedate
    """

    cursor = connection.execute(sql, (START_DATE, END_DATE))

    for row in cursor:
        yield dict(row)


def build_duplicate_report(rows):
    groups = defaultdict(list)

    for row in rows:
        key = (row["secid"], row["boardid"], row["tradedate"])
        groups[key].append(row)

    result = []

    for (secid, boardid, tradedate), group in groups.items():
        if len(group) > 1:
            result.append({
                "secid": secid,
                "boardid": boardid,
                "tradedate": tradedate,
                "row_count": len(group),
            })

    return result


def build_cross_board_report(rows):
    groups = defaultdict(list)

    for row in rows:
        key = (row["secid"], row["tradedate"])
        groups[key].append(row)

    results = []

    for (secid, tradedate), group in groups.items():
        if len(group) < 2:
            continue

        boards = sorted({
            row["boardid"] for row in group
        })

        if len(boards) < 2:
            continue

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a = group[i]
                b = group[j]

                differing = []

                for field in COMPARE_FIELDS:
                    if field not in a or field not in b:
                        continue

                    if not numeric_equal(a[field], b[field]):
                        differing.append(field)

                results.append({
                    "secid": secid,
                    "tradedate": tradedate,
                    "board_a": a["boardid"],
                    "board_b": b["boardid"],
                    "numtrades_a": a["numtrades"],
                    "numtrades_b": b["numtrades"],
                    "close_a": a["close"],
                    "close_b": b["close"],
                    "differing_fields": "|".join(differing),
                    "has_differences": bool(differing),
                })

    return results


def build_coverage(rows):
    ticker_data = defaultdict(list)
    year_data = defaultdict(list)
    market_dates = set()

    for row in rows:
        secid = row["secid"]
        date = row["tradedate"]
        year = date[:4]

        ticker_data[secid].append(row)
        year_data[(secid, year)].append(row)
        market_dates.add(date)

    sorted_market_dates = sorted(market_dates)
    market_index = {
        date: index
        for index, date in enumerate(sorted_market_dates)
    }

    ticker_coverage = []
    ticker_year_coverage = []
    gap_intervals = []

    for secid, ticker_rows in ticker_data.items():
        dates = sorted({
            row["tradedate"] for row in ticker_rows
        })

        first_date = dates[0]
        last_date = dates[-1]

        start_idx = market_index[first_date]
        end_idx = market_index[last_date]

        expected_dates = end_idx - start_idx + 1
        missing_dates = max(0, expected_dates - len(dates))
        missing_ratio = (
            missing_dates / expected_dates
            if expected_dates else 0
        )

        boards = sorted({
            row["boardid"] for row in ticker_rows
        })

        ticker_coverage.append({
            "secid": secid,
            "first_date": first_date,
            "last_date": last_date,
            "raw_rows": len(ticker_rows),
            "unique_dates": len(dates),
            "boards": "|".join(boards),
            "observed_market_dates_in_span": expected_dates,
            "missing_observed_market_dates": missing_dates,
            "missing_ratio": round(missing_ratio, 6),
            "history_incomplete_candidate": missing_ratio > 0.05,
        })

        # Find gaps in the database-observed market calendar.
        present = set(dates)
        previous_present_idx = None

        for date in dates:
            current_idx = market_index[date]

            if previous_present_idx is not None:
                gap_size = current_idx - previous_present_idx - 1

                if gap_size >= GAP_MIN_MARKET_DATES:
                    gap_intervals.append({
                        "secid": secid,
                        "gap_start_after": sorted_market_dates[
                            previous_present_idx
                        ],
                        "gap_end_before": date,
                        "missing_observed_market_dates": gap_size,
                        "calendar_basis": "dates observed across database",
                        "not_proven_missing": True,
                    })

            previous_present_idx = current_idx

        for (ticker, year), year_rows in year_data.items():
            if ticker != secid:
                continue

            year_dates = sorted({
                row["tradedate"] for row in year_rows
            })

            ticker_year_coverage.append({
                "secid": secid,
                "year": year,
                "raw_rows": len(year_rows),
                "unique_dates": len(year_dates),
                "first_date": year_dates[0],
                "last_date": year_dates[-1],
                "boards": "|".join(sorted({
                    row["boardid"] for row in year_rows
                })),
            })

    return (
        ticker_coverage,
        ticker_year_coverage,
        gap_intervals,
        sorted_market_dates,
    )


def build_return_flags(rows, flags):
    """
    Investigative screen only:
    compares consecutive active observations within SECID+BOARDID.
    Large changes are not automatically errors.
    """
    previous_active = {}

    for row in rows:
        key = (row["secid"], row["boardid"])

        trades = row["numtrades"]
        close = row["close"]

        if (
            trades is None
            or trades <= 0
            or not is_finite_number(close)
            or close <= 0
        ):
            continue

        previous = previous_active.get(key)

        if previous is not None:
            previous_date, previous_close = previous
            change = float(close) / previous_close - 1.0

            if abs(change) >= SUSPICIOUS_RETURN_THRESHOLD:
                add_flag(
                    flags,
                    row,
                    "large_change_between_active_observations",
                    "investigate",
                    (
                        f"Close changed by {change:.2%} since "
                        f"{previous_date}; this is not necessarily "
                        "a one-session return or a data error."
                    ),
                )

        previous_active[key] = (
            row["tradedate"], float(close)
        )


def main():
    args = parse_args()

    db_path = Path(args.db)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("MOEX DATA QUALITY AUDIT: 2005-2024")
    print("=" * 68)
    print(f"Database: {db_path}")
    print(f"Output:   {output_dir}")
    print("Mode:     READ ONLY")
    print()

    connection = open_readonly_database(db_path)

    try:
        columns = inspect_schema(connection)
        rows = list(fetch_rows(connection, columns))
    finally:
        connection.close()

    if not rows:
        raise RuntimeError(
            "No rows found in the requested date range."
        )

    print(f"Rows loaded: {len(rows):,}")

    flags = []

    for row in rows:
        audit_row(row, flags)

    build_return_flags(rows, flags)

    duplicates = build_duplicate_report(rows)
    cross_board = build_cross_board_report(rows)

    (
        ticker_coverage,
        ticker_year_coverage,
        gap_intervals,
        market_dates,
    ) = build_coverage(rows)

    flag_counts = Counter(
        flag["flag_type"] for flag in flags
    )

    board_counts = Counter(
        row["boardid"] for row in rows
    )

    active_rows = sum(
        1 for row in rows
        if row["numtrades"] is not None
        and row["numtrades"] > 0
    )

    no_trade_rows = sum(
        1 for row in rows
        if row["numtrades"] == 0
    )

    unique_tickers = len({
        row["secid"] for row in rows
    })

    unique_ticker_dates = len({
        (row["secid"], row["tradedate"])
        for row in rows
    })

    cross_board_mismatches = sum(
        1 for row in cross_board
        if row["has_differences"]
    )

    summary = {
        "audit_name": "MOEX Data Quality Audit 2005-2024",
        "period_start": START_DATE,
        "period_end": END_DATE,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "database_modified": False,
        "cleaning_performed": False,
        "rows": len(rows),
        "unique_tickers": unique_tickers,
        "unique_boards": len(board_counts),
        "observed_market_dates": len(market_dates),
        "unique_ticker_dates": unique_ticker_dates,
        "active_trade_rows": active_rows,
        "zero_trade_rows": no_trade_rows,
        "duplicate_ticker_board_date_groups": len(duplicates),
        "cross_board_comparison_groups": len(cross_board),
        "cross_board_groups_with_differences": cross_board_mismatches,
        "incomplete_history_candidates": sum(
            1 for row in ticker_coverage
            if row["history_incomplete_candidate"]
        ),
        "gap_intervals_ge_5_observed_dates": len(gap_intervals),
        "data_quality_flag_rows": len(flags),
        "flags_by_type": dict(flag_counts),
        "rows_by_board": dict(board_counts),
        "methodological_notes": [
            "All raw rows are preserved.",
            "No values were corrected or deleted.",
            "Cross-board records are not automatically duplicates.",
            "Observed database dates are a proxy, not an official MOEX calendar.",
            "Large price changes are investigation candidates, not proven errors.",
            "No-trade rows are retained and classified separately.",
            "Corporate actions are not automatically adjusted.",
        ],
    }

    flag_fields = [
        "secid", "boardid", "tradedate",
        "flag_type", "severity", "reason",
        "numtrades", "volume", "value",
        "open", "high", "low", "close",
    ]

    write_csv(
        output_dir / "data_quality_flags.csv",
        flags,
        flag_fields,
    )

    write_csv(
        output_dir / "duplicate_ticker_board_dates.csv",
        duplicates,
        ["secid", "boardid", "tradedate", "row_count"],
    )

    write_csv(
        output_dir / "cross_board_comparison.csv",
        cross_board,
        [
            "secid", "tradedate", "board_a", "board_b",
            "numtrades_a", "numtrades_b",
            "close_a", "close_b",
            "differing_fields", "has_differences",
        ],
    )

    write_csv(
        output_dir / "ticker_coverage.csv",
        ticker_coverage,
        [
            "secid", "first_date", "last_date",
            "raw_rows", "unique_dates", "boards",
            "observed_market_dates_in_span",
            "missing_observed_market_dates",
            "missing_ratio", "history_incomplete_candidate",
        ],
    )

    write_csv(
        output_dir / "ticker_year_coverage.csv",
        ticker_year_coverage,
        [
            "secid", "year", "raw_rows", "unique_dates",
            "first_date", "last_date", "boards",
        ],
    )

    write_csv(
        output_dir / "gap_intervals.csv",
        gap_intervals,
        [
            "secid", "gap_start_after", "gap_end_before",
            "missing_observed_market_dates",
            "calendar_basis", "not_proven_missing",
        ],
    )

    with (output_dir / "summary.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            summary,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print("=" * 68)
    print("AUDIT SUMMARY")
    print("=" * 68)
    print(f"Raw rows:                       {len(rows):,}")
    print(f"Unique tickers:                 {unique_tickers:,}")
    print(f"Observed market dates:          {len(market_dates):,}")
    print(f"Active trade rows:              {active_rows:,}")
    print(f"Zero-trade rows:                {no_trade_rows:,}")
    print(f"Duplicate groups:               {len(duplicates):,}")
    print(f"Cross-board groups:             {len(cross_board):,}")
    print(f"Cross-board mismatches:         {cross_board_mismatches:,}")
    print(
        "Incomplete history candidates: "
        f"{summary['incomplete_history_candidates']:,}"
    )
    print(f"Gap intervals:                  {len(gap_intervals):,}")
    print(f"Quality flag rows:              {len(flags):,}")

    print("\nRows by board:")
    for board, count in sorted(board_counts.items()):
        print(f"  {board}: {count:,}")

    print("\nFlags by type:")
    for flag_type, count in flag_counts.most_common():
        print(f"  {flag_type}: {count:,}")

    print("\nReports:")
    for filename in (
        "summary.json",
        "data_quality_flags.csv",
        "duplicate_ticker_board_dates.csv",
        "cross_board_comparison.csv",
        "ticker_coverage.csv",
        "ticker_year_coverage.csv",
        "gap_intervals.csv",
    ):
        print(f"  {output_dir / filename}")

    print("\nDatabase modified: NO")
    print("Cleaning performed: NO")
    print("AUDIT COMPLETE")


if __name__ == "__main__":
    main()
