# Path: scripts/audit_raw_database.py
# Description: Read-only audit of raw MOEX daily bars in SQLite.
#              Produces coverage, gap, duplicate-board and OHLCV quality reports.

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DB = PROJECT_ROOT / "data" / "market_data" / "moex_daily_raw.sqlite"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "interim" / "raw_database_audit"

TABLE_NAME = "daily_bars_raw"

PRICE_COLUMNS = (
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
)

COMPARISON_COLUMNS = (
    "NUMTRADES",
    "VALUE",
    "VOLUME",
    "OPEN",
    "HIGH",
    "LOW",
    "LEGALCLOSEPRICE",
    "WAPRICE",
    "CLOSE",
    "MARKETPRICE2",
    "MARKETPRICE3",
    "ADMITTEDQUOTE",
    "ADMITTEDVALUE",
)

REQUIRED_COLUMNS = {
    "SECID",
    "BOARDID",
    "TRADEDATE",
    "NUMTRADES",
    "VOLUME",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit of the MOEX SQLite raw database."
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help="Path to SQLite database.",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for audit reports.",
    )

    return parser.parse_args()


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(result):
        return None

    return result


def date_string(value: Any) -> str | None:
    if value is None:
        return None

    result = str(value)[:10]

    try:
        datetime.strptime(result, "%Y-%m-%d")
    except ValueError:
        return None

    return result


def values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True

    if a is None or b is None:
        return False

    fa = safe_float(a)
    fb = safe_float(b)

    if fa is not None and fb is not None:
        return math.isclose(
            fa,
            fb,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )

    return str(a) == str(b)


def consecutive_groups(
    missing_indices: list[int],
) -> list[list[int]]:
    if not missing_indices:
        return []

    groups = [[missing_indices[0]]]

    for index in missing_indices[1:]:
        if index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])

    return groups

def compact_json(value: Any) -> str:
    """Compact JSON serialization for embedding structured data in CSV cells."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def check_ohlcv(
    row: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """
    Returns:
        (flag_type, severity, reason)
    """

    flags = []

    prices = {
        column: safe_float(row.get(column))
        for column in PRICE_COLUMNS
    }

    for column, value in prices.items():
        if value is not None and value < 0:
            flags.append(
                (
                    "negative_price",
                    "high",
                    f"{column} is negative",
                )
            )

        if value == 0:
            flags.append(
                (
                    "zero_price",
                    "medium",
                    f"{column} equals zero",
                )
            )

    high = prices["HIGH"]
    low = prices["LOW"]
    open_price = prices["OPEN"]
    close = prices["CLOSE"]

    if high is not None and low is not None and high < low:
        flags.append(
            (
                "high_below_low",
                "high",
                "HIGH is below LOW",
            )
        )

    if high is not None and low is not None:
        if open_price is not None and not low <= open_price <= high:
            flags.append(
                (
                    "open_outside_range",
                    "medium",
                    "OPEN is outside LOW-HIGH range",
                )
            )

        if close is not None and not low <= close <= high:
            flags.append(
                (
                    "close_outside_range",
                    "medium",
                    "CLOSE is outside LOW-HIGH range",
                )
            )

    numtrades = safe_float(row.get("NUMTRADES"))
    volume = safe_float(row.get("VOLUME"))
    value = safe_float(row.get("VALUE"))

    if numtrades is not None and numtrades < 0:
        flags.append(
            (
                "negative_numtrades",
                "high",
                "NUMTRADES is negative",
            )
        )

    if volume is not None and volume < 0:
        flags.append(
            (
                "negative_volume",
                "high",
                "VOLUME is negative",
            )
        )

    if value is not None and value < 0:
        flags.append(
            (
                "negative_value",
                "high",
                "VALUE is negative",
            )
        )

    if numtrades == 0:
        flags.append(
            (
                "zero_trades",
                "info",
                "NUMTRADES equals zero",
            )
        )

    if volume == 0:
        flags.append(
            (
                "zero_volume",
                "info",
                "VOLUME equals zero",
            )
        )

    if (
        numtrades is not None
        and numtrades > 0
        and volume == 0
    ):
        flags.append(
            (
                "trades_without_volume",
                "medium",
                "NUMTRADES > 0 while VOLUME equals zero",
            )
        )

    if (
        numtrades is not None
        and numtrades == 0
        and volume is not None
        and volume > 0
    ):
        flags.append(
            (
                "volume_without_trades",
                "medium",
                "VOLUME > 0 while NUMTRADES equals zero",
            )
        )

    return flags


def inspect_database(connection):
    """
    Validate the raw database schema and return column names and row count.
    Column name comparison is case-insensitive.
    """

    # Check that the required table exists.
    table = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'daily_bars_raw'
        """
    ).fetchone()

    if table is None:
        raise RuntimeError(
            "Required table 'daily_bars_raw' does not exist."
        )

    # Read actual table schema.
    schema_rows = connection.execute(
        "PRAGMA table_info(daily_bars_raw)"
    ).fetchall()

    # Preserve original column names, but normalize for validation.
    columns = [row["name"] for row in schema_rows]
    normalized_columns = {
        column.lower()
        for column in columns
    }

    required_columns = {
        "secid",
        "boardid",
        "tradedate",
        "numtrades",
        "volume",
        "open",
        "high",
        "low",
        "close",
    }

    missing_columns = required_columns - normalized_columns

    if missing_columns:
        raise RuntimeError(
            "Missing required columns: "
            f"{sorted(missing_columns)}"
        )

    # Count raw observations.
    row_count = connection.execute(
        "SELECT COUNT(*) FROM daily_bars_raw"
    ).fetchone()[0]

    return columns, row_count


def run_audit(
    db_path: Path,
    output_dir: Path,
) -> dict[str, Any]:

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database does not exist: {db_path}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    # Explicitly prohibit writes through this connection.
    connection.execute("PRAGMA query_only = ON")

    try:
        columns, total_rows = inspect_database(connection)

        available_columns = {
            str(column).upper(): str(column)
            for column in columns
        }

        desired_columns = [
            "SECID",
            "BOARDID",
            "TRADEDATE",
            "TRADINGSESSION",
            "NUMTRADES",
            "VALUE",
            "VOLUME",
            "OPEN",
            "LOW",
            "HIGH",
            "LEGALCLOSEPRICE",
            "WAPRICE",
            "CLOSE",
            "MARKETPRICE2",
            "MARKETPRICE3",
            "ADMITTEDQUOTE",
            "ADMITTEDVALUE",
        ]

        selected_columns = [
            f'"{available_columns[column]}" AS "{column}"'
            for column in desired_columns
            if column in available_columns
        ]

        required_columns_check = {
            "SECID",
            "BOARDID",
            "TRADEDATE",
            "NUMTRADES",
            "VOLUME",
            "OPEN",
            "HIGH",
            "LOW",
            "CLOSE",
        }

        missing_columns = required_columns_check - set(available_columns)

        if missing_columns:
            raise RuntimeError(
                f"Missing required columns: {sorted(missing_columns)}"
            )

        sql = (
            f"SELECT {', '.join(selected_columns)} "
            f"FROM {TABLE_NAME} "
            "ORDER BY SECID, TRADEDATE, BOARDID"
        )

        ticker_dates = defaultdict(set)
        ticker_board_dates = defaultdict(set)

        ticker_year_stats = defaultdict(
            lambda: {
                "rows": 0,
                "dates": set(),
                "boards": set(),
            }
        )

        board_year_stats = defaultdict(
            lambda: {
                "rows": 0,
                "dates": set(),
            }
        )

        ticker_rows = Counter()
        board_rows = Counter()
        session_rows = Counter()

        ticker_date_rows = defaultdict(list)

        data_quality_flags = []

        min_dates = {}
        max_dates = {}

        invalid_date_rows = 0
        rows_processed = 0

        for sqlite_row in connection.execute(sql):
            row = dict(sqlite_row)
            rows_processed += 1

            secid = str(row["SECID"])
            board = str(row["BOARDID"])

            trade_date = date_string(row["TRADEDATE"])

            ticker_rows[secid] += 1
            board_rows[board] += 1

            session = row.get("TRADINGSESSION")
            session_rows[str(session)] += 1

            if trade_date is None:
                invalid_date_rows += 1
                continue

            year = trade_date[:4]

            ticker_dates[secid].add(trade_date)
            ticker_board_dates[(secid, board)].add(trade_date)

            ticker_date_rows[(secid, trade_date)].append(row)

            ticker_year = ticker_year_stats[(secid, year)]
            ticker_year["rows"] += 1
            ticker_year["dates"].add(trade_date)
            ticker_year["boards"].add(board)

            board_year = board_year_stats[(board, year)]
            board_year["rows"] += 1
            board_year["dates"].add(trade_date)

            if secid not in min_dates or trade_date < min_dates[secid]:
                min_dates[secid] = trade_date

            if secid not in max_dates or trade_date > max_dates[secid]:
                max_dates[secid] = trade_date

            for flag_type, severity, reason in check_ohlcv(row):
                data_quality_flags.append(
                    {
                        "SECID": secid,
                        "BOARDID": board,
                        "TRADEDATE": trade_date,
                        "flag_type": flag_type,
                        "severity": severity,
                        "reason": reason,
                        "NUMTRADES": row.get("NUMTRADES"),
                        "VOLUME": row.get("VOLUME"),
                        "OPEN": row.get("OPEN"),
                        "HIGH": row.get("HIGH"),
                        "LOW": row.get("LOW"),
                        "CLOSE": row.get("CLOSE"),
                    }
                )

        # ------------------------------------------------------------
        # 1. Global observed market calendar
        # ------------------------------------------------------------

        global_calendar = sorted(
            {
                date
                for dates in ticker_dates.values()
                for date in dates
            }
        )

        calendar_index = {
            date: index
            for index, date in enumerate(global_calendar)
        }

        # ------------------------------------------------------------
        # 2. Ticker coverage
        # ------------------------------------------------------------

        ticker_coverage = []
        date_gaps = []

        incomplete_tickers = 0

        for secid, dates in sorted(ticker_dates.items()):
            sorted_dates = sorted(dates)

            first_date = sorted_dates[0]
            last_date = sorted_dates[-1]

            first_index = calendar_index[first_date]
            last_index = calendar_index[last_date]

            expected_dates = global_calendar[
                first_index:last_index + 1
            ]

            observed = set(sorted_dates)

            missing_dates = [
                date
                for date in expected_dates
                if date not in observed
            ]

            observed_count = len(observed)
            missing_count = len(missing_dates)

            denominator = observed_count + missing_count

            missing_ratio = (
                missing_count / denominator
                if denominator
                else 0.0
            )

            # This is a heuristic based on the dataset's observed
            # global calendar, not an official MOEX trading calendar.
            history_incomplete = missing_ratio > 0.05

            if history_incomplete:
                incomplete_tickers += 1

            ticker_boards = sorted(
                board
                for ticker, board in ticker_board_dates
                if ticker == secid
            )

            ticker_coverage.append(
                {
                    "SECID": secid,
                    "first_date": first_date,
                    "last_date": last_date,
                    "raw_rows": ticker_rows[secid],
                    "unique_dates": observed_count,
                    "approved_boards_present": "|".join(ticker_boards),
                    "observed_market_dates_in_span": len(expected_dates),
                    "missing_observed_market_dates": missing_count,
                    "missing_ratio": round(missing_ratio, 6),
                    "history_incomplete": history_incomplete,
                }
            )

            missing_indices = [
                calendar_index[date]
                for date in missing_dates
            ]

            for group in consecutive_groups(missing_indices):
                gap_dates = [
                    global_calendar[index]
                    for index in group
                ]

                date_gaps.append(
                    {
                        "SECID": secid,
                        "gap_start": gap_dates[0],
                        "gap_end": gap_dates[-1],
                        "missing_market_dates": len(gap_dates),
                        "previous_observed_date": (
                            global_calendar[group[0] - 1]
                            if group[0] > 0
                            else None
                        ),
                        "next_observed_date": (
                            global_calendar[group[-1] + 1]
                            if group[-1] + 1 < len(global_calendar)
                            else None
                        ),
                    }
                )

        # ------------------------------------------------------------
        # 3. Ticker/year coverage
        # ------------------------------------------------------------

        ticker_year_coverage = []

        for (secid, year), stats in sorted(ticker_year_stats.items()):
            dates = sorted(stats["dates"])

            ticker_year_coverage.append(
                {
                    "SECID": secid,
                    "year": year,
                    "raw_rows": stats["rows"],
                    "unique_dates": len(stats["dates"]),
                    "first_date": dates[0],
                    "last_date": dates[-1],
                    "boards": "|".join(sorted(stats["boards"])),
                }
            )

        # ------------------------------------------------------------
        # 4. Board/year coverage
        # ------------------------------------------------------------

        board_year_coverage = []

        for (board, year), stats in sorted(board_year_stats.items()):
            dates = sorted(stats["dates"])

            board_year_coverage.append(
                {
                    "BOARDID": board,
                    "year": year,
                    "raw_rows": stats["rows"],
                    "unique_dates": len(stats["dates"]),
                    "first_date": dates[0],
                    "last_date": dates[-1],
                }
            )

        # ------------------------------------------------------------
        # 5. Duplicate ticker/date observations
        # ------------------------------------------------------------

        duplicate_ticker_dates = []
        overlapping_board_dates = []

        duplicate_groups = 0
        cross_board_groups = 0
        cross_board_mismatch_groups = 0

        for (secid, trade_date), rows in sorted(ticker_date_rows.items()):
            if len(rows) <= 1:
                continue

            boards = sorted(
                {
                    str(row["BOARDID"])
                    for row in rows
                }
            )

            duplicate_groups += 1

            duplicate_ticker_dates.append(
                {
                    "SECID": secid,
                    "TRADEDATE": trade_date,
                    "row_count": len(rows),
                    "board_count": len(boards),
                    "boards": "|".join(boards),
                    "sessions": "|".join(
                        sorted(
                            {
                                str(row.get("TRADINGSESSION"))
                                for row in rows
                            }
                        )
                    ),
                }
            )

            if len(boards) <= 1:
                continue

            cross_board_groups += 1

            differing_fields = []

            for column in COMPARISON_COLUMNS:
                if column not in columns:
                    continue

                values = [
                    row.get(column)
                    for row in rows
                ]

                reference = values[0]

                if any(
                    not values_equal(reference, value)
                    for value in values[1:]
                ):
                    differing_fields.append(column)

            if differing_fields:
                cross_board_mismatch_groups += 1

            overlapping_board_dates.append(
                {
                    "SECID": secid,
                    "TRADEDATE": trade_date,
                    "boards": "|".join(boards),
                    "row_count": len(rows),
                    "differing_fields": "|".join(differing_fields),
                    "has_differences": bool(differing_fields),
                    "board_values": compact_json(
                        [
                            {
                                "BOARDID": row.get("BOARDID"),
                                **{
                                    column: row.get(column)
                                    for column in COMPARISON_COLUMNS
                                    if column in columns
                                },
                            }
                            for row in rows
                        ]
                    ),
                }
            )

        # ------------------------------------------------------------
        # 6. Summary
        # ------------------------------------------------------------

        severity_counts = Counter(
            row["severity"]
            for row in data_quality_flags
        )

        flag_type_counts = Counter(
            row["flag_type"]
            for row in data_quality_flags
        )

        summary = {
            "database": str(db_path),
            "table": TABLE_NAME,
            "audit_mode": "read_only",
            "raw_rows_in_database": total_rows,
            "rows_processed": rows_processed,
            "invalid_date_rows": invalid_date_rows,
            "unique_secids": len(ticker_dates),
            "unique_boards": len(board_rows),
            "global_observed_market_dates": len(global_calendar),
            "global_first_date": (
                global_calendar[0] if global_calendar else None
            ),
            "global_last_date": (
                global_calendar[-1] if global_calendar else None
            ),
            "rows_by_board": dict(sorted(board_rows.items())),
            "rows_by_tradingsession": dict(
                sorted(session_rows.items())
            ),
            "duplicate_ticker_date_groups": duplicate_groups,
            "cross_board_overlap_groups": cross_board_groups,
            "cross_board_mismatch_groups": cross_board_mismatch_groups,
            "tickers_with_history_incomplete_flag": incomplete_tickers,
            "date_gap_intervals": len(date_gaps),
            "data_quality_flag_rows": len(data_quality_flags),
            "data_quality_flags_by_severity": dict(severity_counts),
            "data_quality_flags_by_type": dict(flag_type_counts),
            "methodology": {
                "calendar": (
                    "Distinct dates observed in the downloaded database."
                ),
                "gap_interpretation": (
                    "Missing ticker observations inside its observed "
                    "first/last date interval are candidates for review, "
                    "not confirmed missing bars."
                ),
                "history_incomplete_threshold": (
                    "missing / (observed + missing) > 5%"
                ),
                "history_incomplete_warning": (
                    "Heuristic only. Illiquid securities may naturally "
                    "have no row on some market dates."
                ),
                "cross_board_comparison": (
                    "Exact comparison with numeric tolerance 1e-9."
                ),
                "cleaning_performed": False,
                "database_modified": False,
            },
        }

        # ------------------------------------------------------------
        # 7. Write reports
        # ------------------------------------------------------------

        write_csv(
            output_dir / "ticker_coverage.csv",
            ticker_coverage,
            [
                "SECID",
                "first_date",
                "last_date",
                "raw_rows",
                "unique_dates",
                "approved_boards_present",
                "observed_market_dates_in_span",
                "missing_observed_market_dates",
                "missing_ratio",
                "history_incomplete",
            ],
        )

        write_csv(
            output_dir / "ticker_year_coverage.csv",
            ticker_year_coverage,
            [
                "SECID",
                "year",
                "raw_rows",
                "unique_dates",
                "first_date",
                "last_date",
                "boards",
            ],
        )

        write_csv(
            output_dir / "board_year_coverage.csv",
            board_year_coverage,
            [
                "BOARDID",
                "year",
                "raw_rows",
                "unique_dates",
                "first_date",
                "last_date",
            ],
        )

        write_csv(
            output_dir / "date_gaps.csv",
            date_gaps,
            [
                "SECID",
                "gap_start",
                "gap_end",
                "missing_market_dates",
                "previous_observed_date",
                "next_observed_date",
            ],
        )

        write_csv(
            output_dir / "duplicate_ticker_dates.csv",
            duplicate_ticker_dates,
            [
                "SECID",
                "TRADEDATE",
                "row_count",
                "board_count",
                "boards",
                "sessions",
            ],
        )

        write_csv(
            output_dir / "overlapping_board_dates.csv",
            overlapping_board_dates,
            [
                "SECID",
                "TRADEDATE",
                "boards",
                "row_count",
                "differing_fields",
                "has_differences",
                "board_values",
            ],
        )

        write_csv(
            output_dir / "data_quality_flags.csv",
            data_quality_flags,
            [
                "SECID",
                "BOARDID",
                "TRADEDATE",
                "flag_type",
                "severity",
                "reason",
                "NUMTRADES",
                "VOLUME",
                "OPEN",
                "HIGH",
                "LOW",
                "CLOSE",
            ],
        )

        with (output_dir / "audit_summary.json").open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                summary,
                file,
                ensure_ascii=False,
                indent=2,
            )

        return summary

    finally:
        connection.close()


def print_summary(summary: dict[str, Any]) -> None:
    print()
    print("=" * 58)
    print("MOEX RAW DATABASE AUDIT")
    print("=" * 58)

    print(f"Raw rows:                    {summary['raw_rows_in_database']:,}")
    print(f"Rows processed:              {summary['rows_processed']:,}")
    print(f"Unique SECIDs:               {summary['unique_secids']:,}")
    print(f"Unique boards:               {summary['unique_boards']}")
    print(
        "Observed market dates:      "
        f"{summary['global_observed_market_dates']:,}"
    )

    print(
        "Date range:                  "
        f"{summary['global_first_date']} - "
        f"{summary['global_last_date']}"
    )

    print("-" * 58)

    print(
        "Duplicate ticker/date groups: "
        f"{summary['duplicate_ticker_date_groups']:,}"
    )

    print(
        "Cross-board overlaps:         "
        f"{summary['cross_board_overlap_groups']:,}"
    )

    print(
        "Cross-board mismatches:       "
        f"{summary['cross_board_mismatch_groups']:,}"
    )

    print(
        "Incomplete history flags:    "
        f"{summary['tickers_with_history_incomplete_flag']:,}"
    )

    print(
        "Date gap intervals:           "
        f"{summary['date_gap_intervals']:,}"
    )

    print(
        "Data quality flag rows:       "
        f"{summary['data_quality_flag_rows']:,}"
    )

    print("-" * 58)

    print("Rows by board:")

    for board, count in summary["rows_by_board"].items():
        print(f"  {board}: {count:,}")

    print("Flags by type:")

    for flag_type, count in sorted(
        summary["data_quality_flags_by_type"].items()
    ):
        print(f"  {flag_type}: {count:,}")

    print("-" * 58)
    print("Database modified: NO")
    print("Cleaning performed: NO")
    print("=" * 58)
    print()


def main() -> int:
    args = parse_args()

    try:
        summary = run_audit(
            db_path=args.db,
            output_dir=args.output_dir,
        )

        print_summary(summary)

        print(f"Reports saved to: {args.output_dir}")

        return 0

    except Exception:
        import traceback
        print("AUDIT FAILED:")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())