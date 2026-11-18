
# Path: scripts/audit_adjusted_daily_bars.py
# Description: Audits adjusted daily bars against the immutable raw database and checks corporate-action boundaries.

import csv
import json
import sqlite3
from collections import Counter
from pathlib import Path


RAW_DB = Path("data/market_data/moex_daily_raw.sqlite")
ADJUSTED_DB = Path("data/market_data/moex_daily_adjusted.sqlite")

REPORT_DIR = Path("data/reports/post_adjustment_audit")
BOUNDARY_CSV = REPORT_DIR / "corporate_action_boundaries.csv"
SUMMARY_JSON = REPORT_DIR / "summary.json"

TABLE = "daily_bars_raw"
ADJUSTED_TABLE = "adjusted_daily_bars"

ACTIONS = [
    ("VTBR", "2024-07-15"),
    ("IRAO", "2015-01-20"),
    ("GMKN", "2024-04-08"),
    ("ROLO", "2023-01-19"),
    ("GEMA", "2024-02-08"),
    ("PLZL", "2025-03-27"),
    ("BELU", "2024-08-22"),
]

PRICE_FIELDS = [
    "open", "high", "low", "close",
    "legalcloseprice", "waprice",
    "marketprice2", "marketprice3",
    "admittedquote",
]

VOLUME_FIELDS = ["volume"]

TOLERANCE = 1e-8


def connect_readonly(path):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def close_enough(a, b):
    if a is None or b is None:
        return a is None and b is None

    return abs(float(a) - float(b)) <= (
        TOLERANCE * max(1.0, abs(float(a)), abs(float(b)))
    )


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def audit_row_values(raw_conn, adjusted_conn):
    fields = [
        "id", "secid", "tradedate", "numtrades",
        "value", "row_hash",
        "price_adjustment_factor",
        "volume_adjustment_factor",
        "adjustment_status",
    ]

    fields += PRICE_FIELDS
    fields += [f"adjusted_{x}" for x in PRICE_FIELDS]
    fields += VOLUME_FIELDS
    fields += [f"adjusted_{x}" for x in VOLUME_FIELDS]

    select_fields = ", ".join(f'"{x}"' for x in fields)

    query = f"""
        SELECT {select_fields}
        FROM "{ADJUSTED_TABLE}"
        ORDER BY id
    """

    cursor = adjusted_conn.execute(query)

    positions = {name: i for i, name in enumerate(fields)}

    errors = Counter()
    checked = 0
    quarantined = 0

    for row in cursor:
        checked += 1

        status = row[positions["adjustment_status"]]
        price_factor = row[positions["price_adjustment_factor"]]
        volume_factor = row[positions["volume_adjustment_factor"]]

        if status == "QUARANTINED_MIXED_SCALE":
            quarantined += 1

            for field in PRICE_FIELDS:
                if field in positions:
                    value = row[positions[f"adjusted_{field}"]]
                    if value is not None:
                        errors["quarantine_price_not_null"] += 1

            continue

        for field in PRICE_FIELDS:
            raw_value = row[positions[field]]
            adjusted_value = row[positions[f"adjusted_{field}"]]

            expected = (
                None if raw_value is None or price_factor is None
                else float(raw_value) * float(price_factor)
            )

            if not close_enough(adjusted_value, expected):
                errors[f"price_formula_{field}"] += 1

        for field in VOLUME_FIELDS:
            raw_value = row[positions[field]]
            adjusted_value = row[positions[f"adjusted_{field}"]]

            expected = (
                None if raw_value is None or volume_factor is None
                else float(raw_value) * float(volume_factor)
            )

            if not close_enough(adjusted_value, expected):
                errors[f"volume_formula_{field}"] += 1

    return {
        "rows_checked": checked,
        "quarantined_rows": quarantined,
        "formula_errors": dict(errors),
    }



def audit_unchanged_fields(raw_conn, adjusted_conn):
    fields = [
        "id",
        "secid",
        "boardid",
        "tradedate",
        "numtrades",
        "value",
        "row_hash",
    ]

    select_fields = ", ".join(f'"{field}"' for field in fields)

    raw_cursor = raw_conn.execute(
        f"""
        SELECT {select_fields}
        FROM "{TABLE}"
        ORDER BY id
        """
    )

    adjusted_cursor = adjusted_conn.execute(
        f"""
        SELECT {select_fields}
        FROM "{ADJUSTED_TABLE}"
        ORDER BY id
        """
    )

    errors = Counter()
    checked = 0
    batch_size = 5000

    while True:
        raw_batch = raw_cursor.fetchmany(batch_size)
        adjusted_batch = adjusted_cursor.fetchmany(batch_size)

        if not raw_batch and not adjusted_batch:
            break

        if len(raw_batch) != len(adjusted_batch):
            errors["batch_row_count_mismatch"] += abs(
                len(raw_batch) - len(adjusted_batch)
            )

        for raw_row, adjusted_row in zip(
            raw_batch,
            adjusted_batch,
        ):
            checked += 1

            for index, field in enumerate(fields):
                if raw_row[index] != adjusted_row[index]:
                    errors[field] += 1

    raw_count = raw_conn.execute(
        f'SELECT COUNT(*) FROM "{TABLE}"'
    ).fetchone()[0]

    adjusted_count = adjusted_conn.execute(
        f'SELECT COUNT(*) FROM "{ADJUSTED_TABLE}"'
    ).fetchone()[0]

    return {
        "joined_rows_checked": checked,
        "raw_rows": raw_count,
        "adjusted_rows": adjusted_count,
        "row_counts_match": raw_count == adjusted_count == checked,
        "unchanged_field_errors": dict(errors),
    }



def audit_ohlc(adjusted_conn):
    query = f"""
        SELECT secid, boardid, tradedate,
               adjusted_open, adjusted_high,
               adjusted_low, adjusted_close,
               adjustment_status
        FROM "{ADJUSTED_TABLE}"
        WHERE adjustment_status != 'QUARANTINED_MIXED_SCALE'
    """

    errors = []
    checked = 0

    for row in adjusted_conn.execute(query):
        secid, boardid, date, open_, high, low, close, status = row
        checked += 1

        values = {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }

        present = {
            key: value
            for key, value in values.items()
            if value is not None
        }

        if len(present) < 4:
            continue

        if high < low:
            errors.append({
                "secid": secid,
                "boardid": boardid,
                "tradedate": date,
                "error": "HIGH_BELOW_LOW",
                "status": status,
            })
            continue

        if high < max(open_, close) or low > min(open_, close):
            errors.append({
                "secid": secid,
                "boardid": boardid,
                "tradedate": date,
                "error": "OHLC_CONTAINMENT_VIOLATION",
                "status": status,
            })

    return {
        "rows_checked": checked,
        "ohlc_violation_count": len(errors),
        "examples": errors[:100],
    }




# Path: scripts/audit_adjusted_daily_bars.py
# Description: Audits corporate action boundaries using nearest active observations and a freshness threshold.

import csv
from datetime import date
from pathlib import Path



# Path: scripts/audit_adjusted_daily_bars.py
# Description: Audits corporate action boundaries using nearest active observations and a 90-day freshness threshold.

def audit_boundaries(conn, max_age_days=90):
    """
    Audit corporate action boundaries.

    ACTIONS format:
        [(ticker, effective_date), ...]

    A valid boundary requires the nearest active observation
    before and after the effective date to be within max_age_days.

    Active observation:
        numtrades > 0 AND adjusted_close IS NOT NULL

    Returns:
        List of rows compatible with the existing BOUNDARY_CSV schema.

    Database is read-only.
    """

    cursor = conn.cursor()
    results = []

    for ticker, effective_date in ACTIONS:
        boundary_date = date.fromisoformat(effective_date)

        cursor.execute(
            """
            SELECT DISTINCT boardid
            FROM adjusted_daily_bars
            WHERE secid = ?
            ORDER BY boardid
            """,
            (ticker,),
        )

        boards = [row[0] for row in cursor.fetchall()]

        if not boards:
            results.append({
                "secid": ticker,
                "boardid": None,
                "effective_date": effective_date,
                "section": "BOUNDARY",
                "tradedate": None,
                "numtrades": None,
                "raw_close": None,
                "adjusted_close": None,
                "raw_volume": None,
                "adjusted_volume": None,
                "value": None,
                "adjustment_status": None,
                "boundary_pair": "BOARD_NOT_ACTIVE_AROUND_ACTION",
            })
            continue

        for boardid in boards:
            cursor.execute(
                """
                SELECT
                    tradedate,
                    numtrades,
                    close,
                    adjusted_close,
                    volume,
                    adjusted_volume,
                    value,
                    adjustment_status
                FROM adjusted_daily_bars
                WHERE secid = ?
                  AND boardid = ?
                  AND numtrades > 0
                  AND adjusted_close IS NOT NULL
                  AND tradedate < ?
                ORDER BY tradedate DESC
                LIMIT 1
                """,
                (ticker, boardid, effective_date),
            )
            pre_row = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    tradedate,
                    numtrades,
                    close,
                    adjusted_close,
                    volume,
                    adjusted_volume,
                    value,
                    adjustment_status
                FROM adjusted_daily_bars
                WHERE secid = ?
                  AND boardid = ?
                  AND numtrades > 0
                  AND adjusted_close IS NOT NULL
                  AND tradedate >= ?
                ORDER BY tradedate ASC
                LIMIT 1
                """,
                (ticker, boardid, effective_date),
            )
            post_row = cursor.fetchone()

            pre_date = (
                date.fromisoformat(pre_row[0])
                if pre_row else None
            )
            post_date = (
                date.fromisoformat(post_row[0])
                if post_row else None
            )

            pre_age = (
                (boundary_date - pre_date).days
                if pre_date else None
            )
            post_age = (
                (post_date - boundary_date).days
                if post_date else None
            )

            pre_valid = (
                pre_age is not None
                and pre_age <= max_age_days
            )
            post_valid = (
                post_age is not None
                and post_age <= max_age_days
            )

            if pre_valid and post_valid:
                status = "VALID_BOUNDARY"
            elif not pre_valid and not post_valid:
                status = "BOARD_NOT_ACTIVE_AROUND_ACTION"
            else:
                status = "INCOMPLETE_BOUNDARY"

            # Keep the original CSV schema.
            # Emit the nearest observations, even if stale,
            # but label them so they cannot be mistaken
            # for valid boundary evidence.
            for section, row in (
                ("PRE", pre_row),
                ("POST", post_row),
            ):
                if row is None:
                    continue

                results.append({
                    "secid": ticker,
                    "boardid": boardid,
                    "effective_date": effective_date,
                    "section": section,
                    "tradedate": row[0],
                    "numtrades": row[1],
                    "raw_close": row[2],
                    "adjusted_close": row[3],
                    "raw_volume": row[4],
                    "adjusted_volume": row[5],
                    "value": row[6],
                    "adjustment_status": row[7],
                    "boundary_pair": status,
                })

            # Explicit status row when neither side is valid.
            if status == "BOARD_NOT_ACTIVE_AROUND_ACTION":
                results.append({
                    "secid": ticker,
                    "boardid": boardid,
                    "effective_date": effective_date,
                    "section": "STATUS",
                    "tradedate": None,
                    "numtrades": None,
                    "raw_close": None,
                    "adjusted_close": None,
                    "raw_volume": None,
                    "adjusted_volume": None,
                    "value": None,
                    "adjustment_status": None,
                    "boundary_pair": status,
                })

    return results


def main():
    if not RAW_DB.exists() or not ADJUSTED_DB.exists():
        raise FileNotFoundError("Raw or adjusted database is missing.")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    raw_conn = connect_readonly(RAW_DB)
    adjusted_conn = connect_readonly(ADJUSTED_DB)

    try:
        row_audit = audit_row_values(raw_conn, adjusted_conn)
        unchanged_audit = audit_unchanged_fields(
            raw_conn, adjusted_conn
        )
        ohlc_audit = audit_ohlc(adjusted_conn)
        boundary_rows = audit_boundaries(adjusted_conn)

        write_csv(
            BOUNDARY_CSV,
            boundary_rows,
            [
                "secid", "boardid", "effective_date", "section",
                "tradedate", "numtrades", "raw_close",
                "adjusted_close", "raw_volume", "adjusted_volume",
                "value", "adjustment_status", "boundary_pair",
            ],
        )

        summary = {
            "row_formula_audit": row_audit,
            "unchanged_fields_audit": unchanged_audit,
            "ohlc_audit": ohlc_audit,
            "boundary_rows_written": len(boundary_rows),
            "boundary_file": str(BOUNDARY_CSV),
            "raw_database_modified": False,
            "adjusted_database_modified": False,
            "interpretation": (
                "Boundary jumps require manual interpretation. "
                "A large residual change is not automatically a data error."
            ),
        }

        write_summary = json.dumps(
            summary, ensure_ascii=False, indent=2
        )

        with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
            f.write(write_summary)

        print(write_summary)

    finally:
        raw_conn.close()
        adjusted_conn.close()


if __name__ == "__main__":
    main()
