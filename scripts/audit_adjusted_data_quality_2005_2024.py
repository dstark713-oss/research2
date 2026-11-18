
# Path: scripts/audit_adjusted_data_quality_2005_2024.py
# Description: Read-only data quality audit of corporate-action-adjusted MOEX daily bars for 2005-2024.

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DB_PATH = PROJECT_ROOT / "data" / "market_data" / "moex_daily_adjusted.sqlite"
REPORT_DIR = PROJECT_ROOT / "data" / "reports" / "adjusted_data_quality_2005_2024"

START_DATE = "2005-01-01"
END_DATE = "2024-12-31"

EXCLUDED_TICKERS = {"GAZC", "GAZS", "GAZT"}

# Used only to create a review list of extreme price movements.
# This is NOT a data-error threshold and NOT a trading signal.
LARGE_RETURN_THRESHOLD = 0.50

# Cross-board comparison is descriptive.
# No board is automatically selected as the preferred source.


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def write_csv(path: Path, rows, fieldnames: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0

    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(row)
            count += 1

    return count


def is_missing(value) -> bool:
    return value is None


def safe_float(value):
    if value is None:
        return None
    return float(value)


def excluded_sql():
    placeholders = ",".join("?" for _ in EXCLUDED_TICKERS)
    return f"secid NOT IN ({placeholders})"


def excluded_params():
    return tuple(sorted(EXCLUDED_TICKERS))


# ---------------------------------------------------------------------
# 1. Market date proxy
# ---------------------------------------------------------------------

def load_observed_market_dates(conn) -> list[str]:
    """
    Returns distinct dates present in the adjusted database.

    This is a proxy calendar based on observed database dates.
    It is not claimed to be the official MOEX trading calendar.
    """

    query = f"""
        SELECT DISTINCT tradedate
        FROM adjusted_daily_bars
        WHERE tradedate BETWEEN ? AND ?
          AND {excluded_sql()}
        ORDER BY tradedate
    """

    rows = conn.execute(
        query,
        (START_DATE, END_DATE, *excluded_params()),
    ).fetchall()

    return [row["tradedate"] for row in rows]


# ---------------------------------------------------------------------
# 2. Data quality flags
# ---------------------------------------------------------------------

FLAG_FIELDS = [
    "secid",
    "boardid",
    "tradedate",
    "flag_type",
    "severity",
    "details",
    "adjustment_status",
]


def audit_data_quality_flags(conn):
    output_path = REPORT_DIR / "data_quality_flags.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    query = f"""
        SELECT
            secid,
            boardid,
            tradedate,
            numtrades,
            value,
            volume,
            adjusted_open,
            adjusted_high,
            adjusted_low,
            adjusted_close,
            adjustment_status
        FROM adjusted_daily_bars
        WHERE tradedate BETWEEN ? AND ?
          AND {excluded_sql()}
        ORDER BY secid, boardid, tradedate
    """

    counts = Counter()
    total_rows = 0

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FLAG_FIELDS)
        writer.writeheader()

        cursor = conn.execute(
            query,
            (START_DATE, END_DATE, *excluded_params()),
        )

        for row in cursor:
            total_rows += 1

            secid = row["secid"]
            boardid = row["boardid"]
            tradedate = row["tradedate"]
            numtrades = row["numtrades"]
            volume = row["volume"]
            value = row["value"]
            status = row["adjustment_status"]

            o = row["adjusted_open"]
            h = row["adjusted_high"]
            low = row["adjusted_low"]
            close = row["adjusted_close"]

            def add_flag(flag_type, severity, details):
                writer.writerow({
                    "secid": secid,
                    "boardid": boardid,
                    "tradedate": tradedate,
                    "flag_type": flag_type,
                    "severity": severity,
                    "details": details,
                    "adjustment_status": status,
                })
                counts[flag_type] += 1

            # A quarantined corporate-action bar is expected to have
            # unavailable adjusted prices. It is recorded, not treated
            # as an ordinary OHLC error.
            if status == "QUARANTINED_MIXED_SCALE":
                add_flag(
                    "QUARANTINED_CORPORATE_ACTION",
                    "INFO",
                    "Adjusted prices unavailable due to mixed-scale corporate-action bar",
                )

            # Negative raw activity fields.
            if numtrades is not None and numtrades < 0:
                add_flag(
                    "NEGATIVE_NUMTRADES",
                    "ERROR",
                    f"numtrades={numtrades}",
                )

            if volume is not None and volume < 0:
                add_flag(
                    "NEGATIVE_VOLUME",
                    "ERROR",
                    f"volume={volume}",
                )

            if value is not None and value < 0:
                add_flag(
                    "NEGATIVE_VALUE",
                    "ERROR",
                    f"value={value}",
                )

            active = numtrades is not None and numtrades > 0

            if not active:
                # Empty OHLC on zero-trade rows is normal.
                # Positive adjusted prices without trades are flagged.
                if any(v is not None for v in (o, h, low, close)):
                    add_flag(
                        "NO_TRADE_WITH_ADJUSTED_OHLC",
                        "REVIEW",
                        "Adjusted OHLC exists despite numtrades <= 0 or missing",
                    )
                continue

            # Quarantined bars are handled separately.
            if status == "QUARANTINED_MIXED_SCALE":
                continue

            ohlc = {
                "open": o,
                "high": h,
                "low": low,
                "close": close,
            }

            missing = [
                name for name, value_ in ohlc.items()
                if value_ is None
            ]

            if missing:
                add_flag(
                    "ACTIVE_MISSING_OHLC",
                    "ERROR",
                    "Missing adjusted fields: " + ",".join(missing),
                )
                continue

            if any(value_ <= 0 for value_ in ohlc.values()):
                add_flag(
                    "NONPOSITIVE_ACTIVE_OHLC",
                    "ERROR",
                    f"Adjusted OHLC={ohlc}",
                )

            if low > h:
                add_flag(
                    "OHLC_LOW_ABOVE_HIGH",
                    "ERROR",
                    f"adjusted_low={low}; adjusted_high={h}",
                )
            elif (
                low > min(o, close)
                or h < max(o, close)
            ):
                add_flag(
                    "OHLC_CONTAINMENT_VIOLATION",
                    "ERROR",
                    f"OHLC={ohlc}",
                )

    return {
        "rows_scanned": total_rows,
        "flag_counts": dict(counts),
        "total_flag_rows": sum(counts.values()),
        "output_file": str(output_path.relative_to(PROJECT_ROOT)),
    }


# ---------------------------------------------------------------------
# 3. Ticker coverage
# ---------------------------------------------------------------------

def audit_ticker_coverage(conn):
    query = f"""
        SELECT
            secid,
            MIN(CASE WHEN numtrades > 0 THEN tradedate END) AS first_active_date,
            MAX(CASE WHEN numtrades > 0 THEN tradedate END) AS last_active_date,
            COUNT(*) AS total_rows,
            SUM(CASE WHEN numtrades > 0 THEN 1 ELSE 0 END) AS active_rows,
            SUM(CASE WHEN numtrades = 0 THEN 1 ELSE 0 END) AS zero_trade_rows,
            COUNT(DISTINCT CASE
                WHEN numtrades > 0 THEN tradedate
            END) AS active_dates,
            COUNT(DISTINCT boardid) AS board_count,
            GROUP_CONCAT(DISTINCT boardid) AS boards,
            SUM(CASE
                WHEN numtrades > 0
                 AND (
                    adjusted_open IS NULL
                    OR adjusted_high IS NULL
                    OR adjusted_low IS NULL
                    OR adjusted_close IS NULL
                 )
                THEN 1 ELSE 0
            END) AS active_missing_ohlc,
            SUM(CASE
                WHEN adjustment_status = 'QUARANTINED_MIXED_SCALE'
                THEN 1 ELSE 0
            END) AS quarantined_rows
        FROM adjusted_daily_bars
        WHERE tradedate BETWEEN ? AND ?
          AND {excluded_sql()}
        GROUP BY secid
        ORDER BY secid
    """

    rows = conn.execute(
        query,
        (START_DATE, END_DATE, *excluded_params()),
    )

    output_path = REPORT_DIR / "ticker_coverage.csv"

    fields = [
        "secid",
        "first_active_date",
        "last_active_date",
        "total_rows",
        "active_rows",
        "zero_trade_rows",
        "active_dates",
        "board_count",
        "boards",
        "active_missing_ohlc",
        "quarantined_rows",
    ]

    count = write_csv(output_path, (dict(row) for row in rows), fields)

    return {
        "ticker_count": count,
        "output_file": str(output_path.relative_to(PROJECT_ROOT)),
    }


# ---------------------------------------------------------------------
# 4. Large return events
# ---------------------------------------------------------------------

EVENT_FIELDS = [
    "secid",
    "boardid",
    "previous_date",
    "current_date",
    "previous_close",
    "current_close",
    "return_pct",
    "observed_market_sessions_between",
    "event_type",
    "previous_numtrades",
    "current_numtrades",
    "previous_value",
    "current_value",
    "previous_volume",
    "current_volume",
    "previous_adjustment_status",
    "current_adjustment_status",
]


def audit_large_return_events(conn, market_dates):
    """
    Detects absolute close-to-close changes >= threshold.

    A return is classified as ONE_OBSERVED_SESSION only when the
    two observations are adjacent in the database-derived market
    date proxy.

    Otherwise it is classified as MULTI_SESSION_GAP.

    These are event candidates, not confirmed daily returns or errors.
    """

    date_index = {
        tradedate: index
        for index, tradedate in enumerate(market_dates)
    }

    query = f"""
        SELECT
            secid,
            boardid,
            tradedate,
            numtrades,
            value,
            volume,
            adjusted_close,
            adjustment_status
        FROM adjusted_daily_bars
        WHERE tradedate BETWEEN ? AND ?
          AND {excluded_sql()}
          AND numtrades > 0
          AND adjusted_close IS NOT NULL
          AND adjusted_close > 0
        ORDER BY secid, boardid, tradedate
    """

    output_path = REPORT_DIR / "large_return_events.csv"

    counts = Counter()
    event_count = 0

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=EVENT_FIELDS)
        writer.writeheader()

        cursor = conn.execute(
            query,
            (START_DATE, END_DATE, *excluded_params()),
        )

        previous_key = None
        previous_row = None

        for row in cursor:
            key = (row["secid"], row["boardid"])

            if key != previous_key:
                previous_key = key
                previous_row = row
                continue

            current_row = row

            previous_close = float(previous_row["adjusted_close"])
            current_close = float(current_row["adjusted_close"])

            previous_date = previous_row["tradedate"]
            current_date = current_row["tradedate"]

            previous_index = date_index.get(previous_date)
            current_index = date_index.get(current_date)

            if previous_index is None or current_index is None:
                previous_row = current_row
                continue

            sessions_between = current_index - previous_index - 1

            if previous_close > 0:
                return_value = current_close / previous_close - 1

                if abs(return_value) >= LARGE_RETURN_THRESHOLD:
                    event_type = (
                        "ONE_OBSERVED_SESSION"
                        if sessions_between == 0
                        else "MULTI_SESSION_GAP"
                    )

                    writer.writerow({
                        "secid": row["secid"],
                        "boardid": row["boardid"],
                        "previous_date": previous_date,
                        "current_date": current_date,
                        "previous_close": previous_close,
                        "current_close": current_close,
                        "return_pct": return_value * 100,
                        "observed_market_sessions_between": sessions_between,
                        "event_type": event_type,
                        "previous_numtrades": previous_row["numtrades"],
                        "current_numtrades": current_row["numtrades"],
                        "previous_value": previous_row["value"],
                        "current_value": current_row["value"],
                        "previous_volume": previous_row["volume"],
                        "current_volume": current_row["volume"],
                        "previous_adjustment_status": previous_row["adjustment_status"],
                        "current_adjustment_status": current_row["adjustment_status"],
                    })

                    counts[event_type] += 1
                    event_count += 1

            previous_row = current_row

    return {
        "threshold_abs_return_pct": LARGE_RETURN_THRESHOLD * 100,
        "events": event_count,
        "event_type_counts": dict(counts),
        "market_calendar_proxy": "Distinct tradedates observed in adjusted database",
        "output_file": str(output_path.relative_to(PROJECT_ROOT)),
    }


# ---------------------------------------------------------------------
# 5. Cross-board comparison
# ---------------------------------------------------------------------

CROSS_BOARD_FIELDS = [
    "secid",
    "tradedate",
    "board_a",
    "board_b",
    "close_a",
    "close_b",
    "close_difference_pct",
    "numtrades_a",
    "numtrades_b",
    "value_a",
    "value_b",
]


def audit_cross_board_comparison(conn):
    """
    Compares active observations for the same security/date
    across different boards.

    No board is automatically preferred or discarded.
    """

    query = f"""
        SELECT
            a.secid,
            a.tradedate,
            a.boardid AS board_a,
            b.boardid AS board_b,
            a.adjusted_close AS close_a,
            b.adjusted_close AS close_b,
            a.numtrades AS numtrades_a,
            b.numtrades AS numtrades_b,
            a.value AS value_a,
            b.value AS value_b
        FROM adjusted_daily_bars a
        JOIN adjusted_daily_bars b
          ON a.secid = b.secid
         AND a.tradedate = b.tradedate
         AND a.boardid < b.boardid
        WHERE a.tradedate BETWEEN ? AND ?
          AND {excluded_sql().replace("secid", "a.secid")}
          AND a.numtrades > 0
          AND b.numtrades > 0
        ORDER BY a.secid, a.tradedate, a.boardid, b.boardid
    """

    output_path = REPORT_DIR / "cross_board_duplicates.csv"

    count = 0
    comparable = 0
    missing_close = 0

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=CROSS_BOARD_FIELDS)
        writer.writeheader()

        cursor = conn.execute(
            query,
            (START_DATE, END_DATE, *excluded_params()),
        )

        for row in cursor:
            close_a = row["close_a"]
            close_b = row["close_b"]

            difference_pct = None

            if (
                close_a is not None
                and close_b is not None
                and float(close_b) != 0
            ):
                difference_pct = (
                    float(close_a) / float(close_b) - 1
                ) * 100
                comparable += 1
            else:
                missing_close += 1

            writer.writerow({
                "secid": row["secid"],
                "tradedate": row["tradedate"],
                "board_a": row["board_a"],
                "board_b": row["board_b"],
                "close_a": close_a,
                "close_b": close_b,
                "close_difference_pct": difference_pct,
                "numtrades_a": row["numtrades_a"],
                "numtrades_b": row["numtrades_b"],
                "value_a": row["value_a"],
                "value_b": row["value_b"],
            })

            count += 1

    return {
        "cross_board_active_pairs": count,
        "comparable_close_pairs": comparable,
        "pairs_with_missing_close": missing_close,
        "output_file": str(output_path.relative_to(PROJECT_ROOT)),
    }


# ---------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------

def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Adjusted database not found: {DB_PATH}"
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    conn = connect_readonly(DB_PATH)

    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"Adjusted database integrity check failed: {integrity}"
            )

        market_dates = load_observed_market_dates(conn)

        date_range = {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "observed_market_dates": len(market_dates),
            "first_observed_date": market_dates[0] if market_dates else None,
            "last_observed_date": market_dates[-1] if market_dates else None,
        }

        flags_audit = audit_data_quality_flags(conn)
        coverage_audit = audit_ticker_coverage(conn)
        events_audit = audit_large_return_events(conn, market_dates)
        cross_board_audit = audit_cross_board_comparison(conn)

        summary = {
            "audit": "Adjusted MOEX daily data quality audit",
            "period": date_range,
            "database": str(DB_PATH.relative_to(PROJECT_ROOT)),
            "database_integrity_check": integrity,
            "excluded_tickers": sorted(EXCLUDED_TICKERS),
            "raw_database_modified": False,
            "adjusted_database_modified": False,
            "automatic_price_corrections": False,
            "automatic_row_deletions": False,
            "forward_fill_applied": False,
            "winsorization_applied": False,
            "large_return_threshold_pct": LARGE_RETURN_THRESHOLD * 100,
            "large_return_threshold_is_not_an_error_rule": True,
            "market_calendar_limitation": (
                "Distinct dates observed in the database are used as a proxy. "
                "This is not an independently verified official MOEX calendar."
            ),
            "data_quality_flags": flags_audit,
            "ticker_coverage": coverage_audit,
            "large_return_events": events_audit,
            "cross_board_comparison": cross_board_audit,
        }

        summary_path = REPORT_DIR / "summary.json"

        with summary_path.open("w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)

        print(json.dumps(summary, ensure_ascii=False, indent=2))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
