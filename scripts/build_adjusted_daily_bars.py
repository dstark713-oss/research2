
# Path: scripts/build_adjusted_daily_bars.py
# Description: Builds a separate corporate-action-adjusted daily OHLCV database. The raw database is read-only and never modified.

import csv
import json
import os
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


RAW_DB = Path("data/market_data/moex_daily_raw.sqlite")
OUTPUT_DB = Path("data/market_data/moex_daily_adjusted.sqlite")

REPORT_DIR = Path("data/reports/corporate_actions")
REGISTER_CSV = REPORT_DIR / "corporate_actions_register.csv"
AUDIT_CSV = REPORT_DIR / "adjustment_audit.csv"
SUMMARY_JSON = REPORT_DIR / "summary.json"

SOURCE_TABLE = "daily_bars_raw"
TARGET_TABLE = "adjusted_daily_bars"

# new_shares_per_old_share:
#   10.0       = one old share becomes ten new shares (split 1:10)
#   0.01       = one old share becomes 0.01 new shares (consolidation 100:1)
#
# Historical prices are divided by this factor.
# Historical share volume is multiplied by this factor.
#
# effective_date is the first trading date in the new price/share scale.

CORPORATE_ACTIONS = [
    {
        "action_id": "VTBR_2024_5000_TO_1",
        "secid": "VTBR",
        "action_type": "CONSOLIDATION",
        "ratio": "5000:1",
        "new_shares_per_old_share": 1 / 5000,
        "effective_date": "2024-07-15",
        "status": "APPLY",
        "source_url": "",
        "notes": "Confirmed in previous forensic review. Historical prices multiplied by 5000.",
    },
    {
        "action_id": "IRAO_2015_100_TO_1",
        "secid": "IRAO",
        "action_type": "CONSOLIDATION",
        "ratio": "100:1",
        "new_shares_per_old_share": 1 / 100,
        "effective_date": "2015-01-20",
        "status": "APPLY",
        "source_url": "",
        "notes": "Confirmed in previous forensic review. Historical prices multiplied by 100.",
    },
    {
        "action_id": "GMKN_2024_1_TO_100",
        "secid": "GMKN",
        "action_type": "SPLIT",
        "ratio": "1:100",
        "new_shares_per_old_share": 100,
        "effective_date": "2024-04-08",
        "status": "APPLY",
        "source_url": "",
        "notes": (
            "Confirmed 100-for-1 split. April 8 is used as the first "
            "post-action trading bar in the audited daily series."
        ),
    },
    {
        "action_id": "ROLO_2023_1_TO_10",
        "secid": "ROLO",
        "action_type": "SPLIT",
        "ratio": "1:10",
        "new_shares_per_old_share": 10,
        "effective_date": "2023-01-19",
        "status": "APPLY",
        "source_url": "",
        "notes": (
            "Confirmed 1:10 split. Effective trading boundary used in "
            "the daily series: 2023-01-19."
        ),
    },
    {
        "action_id": "GEMA_2024_1_TO_10",
        "secid": "GEMA",
        "action_type": "SPLIT",
        "ratio": "1:10",
        "new_shares_per_old_share": 10,
        "effective_date": "2024-02-08",
        "status": "APPLY",
        "source_url": "",
        "notes": "Confirmed 1:10 split. Historical prices divided by 10.",
    },
    {
        "action_id": "PLZL_2025_1_TO_10",
        "secid": "PLZL",
        "action_type": "SPLIT",
        "ratio": "1:10",
        "new_shares_per_old_share": 10,
        "effective_date": "2025-03-27",
        "status": "APPLY",
        "source_url": (
            "https://www.moex.com/n88888"
        ),
        "notes": (
            "Official MOEX notice: trading in the new scale resumed "
            "2025-03-27. Issuer confirmed the split and conversion "
            "on 2025-03-25."
        ),
    },
    {
        "action_id": "BELU_2024_1_TO_8",
        "secid": "BELU",
        "action_type": "ADDITIONAL_SHARE_DISTRIBUTION",
        "ratio": "1:8",
        "new_shares_per_old_share": 8,
        "effective_date": "2024-08-22",
        "status": "SPECIAL_HANDLING",
        "source_url": "",
        "notes": (
            "Seven additional shares per existing share. Historical "
            "bars before the effective date are adjusted 1:8. "
            "The 2024-08-22 OHLC bar is quarantined because its fields "
            "appear to contain mixed price scales."
        ),
    },
]


PRICE_FIELDS = [
    "open",
    "high",
    "low",
    "close",
    "legalcloseprice",
    "waprice",
    "marketprice2",
    "marketprice3",
    "admittedquote",
]

VOLUME_FIELDS = ["volume"]


def sqlite_readonly_uri(path: Path) -> str:
    absolute = path.resolve().as_posix()
    return f"file:{quote(absolute, safe='/:')}?mode=ro"


def write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_multiply(value, factor):
    if value is None:
        return None

    try:
        return float(value) * factor
    except (TypeError, ValueError):
        return None


def get_action_by_ticker():
    result = {}

    for action in CORPORATE_ACTIONS:
        secid = action["secid"]

        if secid in result:
            raise ValueError(
                f"Multiple corporate actions for {secid}. "
                "This script currently requires explicit chained-action logic."
            )

        result[secid] = action

    return result


def create_target_table(source_conn, target_conn, source_columns):
    table_info = source_conn.execute(
        f"PRAGMA table_info({SOURCE_TABLE})"
    ).fetchall()

    definitions = []

    for column in table_info:
        name = column[1]
        declared_type = column[2] or "TEXT"
        is_primary_key = column[5] == 1

        if is_primary_key:
            definitions.append(
                f'"{name}" {declared_type} PRIMARY KEY'
            )
        else:
            definitions.append(
                f'"{name}" {declared_type}'
            )

    for field in PRICE_FIELDS:
        if field in source_columns:
            definitions.append(f'"adjusted_{field}" REAL')

    for field in VOLUME_FIELDS:
        if field in source_columns:
            definitions.append(f'"adjusted_{field}" REAL')

    definitions.extend([
        '"price_adjustment_factor" REAL',
        '"volume_adjustment_factor" REAL',
        '"corporate_action_id" TEXT',
        '"adjustment_status" TEXT',
    ])

    sql = (
        f'CREATE TABLE "{TARGET_TABLE}" '
        f'({", ".join(definitions)})'
    )

    target_conn.execute(sql)


def build_adjusted_database():
    if not RAW_DB.exists():
        raise FileNotFoundError(f"Raw database not found: {RAW_DB}")

    OUTPUT_DB.parent.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    temp_db = OUTPUT_DB.with_name(
        OUTPUT_DB.stem + ".building.sqlite"
    )

    if temp_db.exists():
        temp_db.unlink()

    source_conn = sqlite3.connect(
        sqlite_readonly_uri(RAW_DB),
        uri=True,
    )

    target_conn = sqlite3.connect(temp_db)

    try:
        source_conn.execute("PRAGMA query_only = ON")

        source_columns_info = source_conn.execute(
            f"PRAGMA table_info({SOURCE_TABLE})"
        ).fetchall()

        if not source_columns_info:
            raise ValueError(
                f"Source table {SOURCE_TABLE} does not exist."
            )

        source_columns = [
            column[1] for column in source_columns_info
        ]

        required_columns = {
            "secid",
            "tradedate",
            "volume",
        }

        missing = required_columns - set(source_columns)

        if missing:
            raise ValueError(
                f"Missing required source columns: {sorted(missing)}"
            )

        actions_by_ticker = get_action_by_ticker()

        create_target_table(
            source_conn,
            target_conn,
            source_columns,
        )

        output_columns = list(source_columns)

        for field in PRICE_FIELDS:
            if field in source_columns:
                output_columns.append(f"adjusted_{field}")

        for field in VOLUME_FIELDS:
            if field in source_columns:
                output_columns.append(f"adjusted_{field}")

        output_columns.extend([
            "price_adjustment_factor",
            "volume_adjustment_factor",
            "corporate_action_id",
            "adjustment_status",
        ])

        quoted_columns = ", ".join(
            f'"{column}"' for column in output_columns
        )

        placeholders = ", ".join(
            "?" for _ in output_columns
        )

        insert_sql = (
            f'INSERT INTO "{TARGET_TABLE}" '
            f'({quoted_columns}) VALUES ({placeholders})'
        )

        select_sql = (
            f'SELECT * FROM "{SOURCE_TABLE}" ORDER BY id'
            if "id" in source_columns
            else f'SELECT * FROM "{SOURCE_TABLE}"'
        )

        source_cursor = source_conn.execute(select_sql)

        index = {
            name: position
            for position, name in enumerate(source_columns)
        }

        row_count = 0
        status_counts = Counter()
        action_counts = Counter()
        audit = []

        action_row_counts = {
            action["action_id"]: {
                "historical_rows_adjusted": 0,
                "effective_date_rows_quarantined": 0,
                "post_action_rows_unadjusted": 0,
            }
            for action in CORPORATE_ACTIONS
        }

        while True:
            batch = source_cursor.fetchmany(5000)

            if not batch:
                break

            output_batch = []

            for raw_row in batch:
                row = list(raw_row)

                secid = row[index["secid"]]
                tradedate = row[index["tradedate"]]

                action = actions_by_ticker.get(secid)

                price_factor = 1.0
                volume_factor = 1.0
                action_id = None
                status = "UNADJUSTED_NO_ACTION"

                if action:
                    action_id = action["action_id"]
                    effective_date = action["effective_date"]
                    share_factor = float(
                        action["new_shares_per_old_share"]
                    )

                    if tradedate is None:
                        status = "UNADJUSTED_DATE_MISSING"

                    elif tradedate < effective_date:
                        price_factor = 1.0 / share_factor
                        volume_factor = share_factor
                        status = "ADJUSTED"

                        action_row_counts[action_id][
                            "historical_rows_adjusted"
                        ] += 1

                    elif (
                        action["status"] == "SPECIAL_HANDLING"
                        and tradedate == effective_date
                    ):
                        status = "QUARANTINED_MIXED_SCALE"

                        action_row_counts[action_id][
                            "effective_date_rows_quarantined"
                        ] += 1

                    else:
                        status = "POST_ACTION_NATIVE_SCALE"

                        action_row_counts[action_id][
                            "post_action_rows_unadjusted"
                        ] += 1

                adjusted_values = []

                for field in PRICE_FIELDS:
                    if field not in index:
                        continue

                    raw_value = row[index[field]]

                    if status == "QUARANTINED_MIXED_SCALE":
                        adjusted_values.append(None)
                    else:
                        adjusted_values.append(
                            safe_multiply(raw_value, price_factor)
                        )

                for field in VOLUME_FIELDS:
                    if field not in index:
                        continue

                    raw_value = row[index[field]]

                    adjusted_values.append(
                        safe_multiply(raw_value, volume_factor)
                    )

                if status == "QUARANTINED_MIXED_SCALE":
                    stored_price_factor = None
                else:
                    stored_price_factor = price_factor

                output_row = (
                    row
                    + adjusted_values
                    + [
                        stored_price_factor,
                        volume_factor,
                        action_id,
                        status,
                    ]
                )

                output_batch.append(output_row)

                row_count += 1
                status_counts[status] += 1

                if action_id:
                    action_counts[action_id] += 1

            target_conn.executemany(
                insert_sql,
                output_batch,
            )

            target_conn.commit()

        target_conn.execute(
            f'CREATE INDEX idx_adjusted_secid_date '
            f'ON "{TARGET_TABLE}" (secid, tradedate)'
        )

        if "boardid" in source_columns:
            target_conn.execute(
                f'CREATE INDEX idx_adjusted_secid_board_date '
                f'ON "{TARGET_TABLE}" (secid, boardid, tradedate)'
            )

        target_conn.commit()

        # Build corporate action audit.
        for action in CORPORATE_ACTIONS:
            counts = action_row_counts[action["action_id"]]

            audit.append({
                "action_id": action["action_id"],
                "secid": action["secid"],
                "ratio": action["ratio"],
                "effective_date": action["effective_date"],
                "status": action["status"],
                **counts,
            })

        # Verify source and target row counts.
        source_count = source_conn.execute(
            f"SELECT COUNT(*) FROM {SOURCE_TABLE}"
        ).fetchone()[0]

        target_count = target_conn.execute(
            f'SELECT COUNT(*) FROM "{TARGET_TABLE}"'
        ).fetchone()[0]

        if source_count != target_count or row_count != source_count:
            raise RuntimeError(
                f"Row count mismatch: source={source_count}, "
                f"target={target_count}, processed={row_count}"
            )

        # Basic integrity checks.
        integrity = target_conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"SQLite integrity check failed: {integrity}"
            )

        target_conn.close()
        source_conn.close()

        # Atomic replacement after successful build and checks.
        os.replace(temp_db, OUTPUT_DB)

        register_rows = []

        for action in CORPORATE_ACTIONS:
            register_rows.append({
                "action_id": action["action_id"],
                "secid": action["secid"],
                "action_type": action["action_type"],
                "ratio": action["ratio"],
                "new_shares_per_old_share": (
                    action["new_shares_per_old_share"]
                ),
                "effective_date": action["effective_date"],
                "status": action["status"],
                "source_url": action["source_url"],
                "notes": action["notes"],
            })

        write_csv(
            REGISTER_CSV,
            register_rows,
            [
                "action_id",
                "secid",
                "action_type",
                "ratio",
                "new_shares_per_old_share",
                "effective_date",
                "status",
                "source_url",
                "notes",
            ],
        )

        write_csv(
            AUDIT_CSV,
            audit,
            [
                "action_id",
                "secid",
                "ratio",
                "effective_date",
                "status",
                "historical_rows_adjusted",
                "effective_date_rows_quarantined",
                "post_action_rows_unadjusted",
            ],
        )

        summary = {
            "run_timestamp_utc": datetime.utcnow().isoformat(),
            "source_database": str(RAW_DB),
            "output_database": str(OUTPUT_DB),
            "source_table": SOURCE_TABLE,
            "output_table": TARGET_TABLE,
            "source_rows": source_count,
            "output_rows": target_count,
            "rows_processed": row_count,
            "row_counts_match": source_count == target_count,
            "sqlite_integrity_check": integrity,
            "adjustment_status_counts": dict(status_counts),
            "corporate_action_row_counts": dict(action_counts),
            "corporate_actions": audit,
            "raw_database_modified": False,
            "raw_database_opened_read_only": True,
            "price_adjustments_applied_only_to_confirmed_actions": True,
            "unconfirmed_actions_adjusted": [],
            "belu_effective_date_bar_quarantined": True,
            "automatic_outlier_removal": False,
            "forward_fill_applied": False,
        }

        write_json(SUMMARY_JSON, summary)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("\nAdjusted daily bars database created successfully.")

    except Exception:
        try:
            target_conn.close()
        except Exception:
            pass

        try:
            source_conn.close()
        except Exception:
            pass

        if temp_db.exists():
            temp_db.unlink()

        raise


def main():
    build_adjusted_database()


if __name__ == "__main__":
    main()
