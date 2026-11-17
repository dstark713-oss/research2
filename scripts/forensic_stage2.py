
# Path: scripts/forensic_stage2.py
# Description: Read-only forensic investigation of MOEX cross-board activity,
#              no-trade records and large price changes, 2005-2024.

from pathlib import Path
from collections import Counter, defaultdict
import sqlite3
import csv
import json
import math

DB_PATH = Path("data/market_data/moex_daily_raw.sqlite")
OUTPUT_DIR = Path("data/reports/forensic_stage2")

START_DATE = "2005-01-01"
END_DATE = "2024-12-31"

THRESHOLD = 0.50


def connect():
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    conn = sqlite3.connect(
        DB_PATH.resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=fields, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)


def valid_price(value):
    return (
        value is not None
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def load_rows(conn):
    sql = """
        SELECT
            secid, boardid, tradedate,
            tradingsession, trade_session_date,
            numtrades, value, volume,
            open, high, low, close, waprice
        FROM daily_bars_raw
        WHERE tradedate BETWEEN ? AND ?
        ORDER BY secid, tradedate, boardid
    """
    return [
        dict(row)
        for row in conn.execute(sql, (START_DATE, END_DATE))
    ]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = connect()
    try:
        rows = load_rows(conn)
    finally:
        conn.close()

    print(f"Loaded rows: {len(rows):,}")

    # ---------------------------------------------------------
    # 1. Cross-board activity classification
    # ---------------------------------------------------------

    grouped = defaultdict(dict)

    for row in rows:
        grouped[(row["secid"], row["tradedate"])][
            row["boardid"]
        ] = row

    cross_rows = []
    cross_summary = Counter()

    for (secid, date), boards in grouped.items():
        if "EQBR" not in boards or "TQBR" not in boards:
            continue

        eq = boards["EQBR"]
        tq = boards["TQBR"]

        eq_active = (
            eq["numtrades"] is not None
            and eq["numtrades"] > 0
        )
        tq_active = (
            tq["numtrades"] is not None
            and tq["numtrades"] > 0
        )

        if eq_active and tq_active:
            category = "BOTH_ACTIVE"
        elif eq_active and not tq_active:
            category = "EQBR_ONLY_ACTIVE"
        elif tq_active and not eq_active:
            category = "TQBR_ONLY_ACTIVE"
        else:
            category = "BOTH_INACTIVE"

        cross_summary[category] += 1

        closes_equal = (
            eq["close"] == tq["close"]
            if eq["close"] is not None and tq["close"] is not None
            else None
        )

        cross_rows.append({
            "secid": secid,
            "tradedate": date,
            "category": category,
            "eqbr_numtrades": eq["numtrades"],
            "tqbr_numtrades": tq["numtrades"],
            "eqbr_volume": eq["volume"],
            "tqbr_volume": tq["volume"],
            "eqbr_value": eq["value"],
            "tqbr_value": tq["value"],
            "eqbr_close": eq["close"],
            "tqbr_close": tq["close"],
            "closes_equal": closes_equal,
        })

    write_csv(
        OUTPUT_DIR / "cross_board_activity.csv",
        cross_rows,
        [
            "secid", "tradedate", "category",
            "eqbr_numtrades", "tqbr_numtrades",
            "eqbr_volume", "tqbr_volume",
            "eqbr_value", "tqbr_value",
            "eqbr_close", "tqbr_close", "closes_equal",
        ],
    )

    # ---------------------------------------------------------
    # 2. No-trade record classification
    # ---------------------------------------------------------

    no_trade_patterns = Counter()
    no_trade_positive_close = []

    for row in rows:
        if row["numtrades"] != 0:
            continue

        ohlc = [
            row["open"], row["high"],
            row["low"], row["close"],
        ]

        if all(value is None for value in ohlc):
            pattern = "ALL_OHLC_NULL"
        elif all(value == 0 for value in ohlc):
            pattern = "ALL_OHLC_ZERO"
        elif (
            row["close"] is not None
            and row["close"] > 0
            and all(
                value is None or value == 0
                for value in ohlc[:3]
            )
        ):
            pattern = "ONLY_CLOSE_POSITIVE"
        elif any(value is None for value in ohlc):
            pattern = "PARTIAL_NULL"
        elif any(value == 0 for value in ohlc):
            pattern = "OTHER_ZERO_PATTERN"
        else:
            pattern = "ALL_OHLC_POSITIVE"

        no_trade_patterns[pattern] += 1

        if valid_price(row["close"]):
            no_trade_positive_close.append({
                "secid": row["secid"],
                "boardid": row["boardid"],
                "tradedate": row["tradedate"],
                "numtrades": row["numtrades"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "value": row["value"],
            })

    write_csv(
        OUTPUT_DIR / "no_trade_positive_close.csv",
        no_trade_positive_close,
        [
            "secid", "boardid", "tradedate", "numtrades",
            "open", "high", "low", "close", "volume", "value",
        ],
    )

    # ---------------------------------------------------------
    # 3. Large changes between consecutive active observations
    # ---------------------------------------------------------

    by_security = defaultdict(list)

    for row in rows:
        if (
            row["numtrades"] is not None
            and row["numtrades"] > 0
            and valid_price(row["close"])
        ):
            by_security[
                (row["secid"], row["boardid"])
            ].append(row)

    large_changes = []

    for (secid, board), security_rows in by_security.items():
        security_rows.sort(key=lambda x: x["tradedate"])

        previous = None

        for row in security_rows:
            if previous is not None:
                change = row["close"] / previous["close"] - 1

                if abs(change) >= THRESHOLD:
                    large_changes.append({
                        "secid": secid,
                        "boardid": board,
                        "previous_date": previous["tradedate"],
                        "current_date": row["tradedate"],
                        "previous_close": previous["close"],
                        "current_close": row["close"],
                        "change_pct": round(change * 100, 4),
                        "previous_numtrades": previous["numtrades"],
                        "current_numtrades": row["numtrades"],
                        "previous_volume": previous["volume"],
                        "current_volume": row["volume"],
                        "previous_value": previous["value"],
                        "current_value": row["value"],
                    })

            previous = row

    large_changes.sort(
        key=lambda x: abs(x["change_pct"]),
        reverse=True,
    )

    write_csv(
        OUTPUT_DIR / "large_price_changes.csv",
        large_changes,
        [
            "secid", "boardid",
            "previous_date", "current_date",
            "previous_close", "current_close",
            "change_pct",
            "previous_numtrades", "current_numtrades",
            "previous_volume", "current_volume",
            "previous_value", "current_value",
        ],
    )

    # ---------------------------------------------------------
    # 4. Positive trades with incomplete OHLC
    # ---------------------------------------------------------

    incomplete_active = []

    for row in rows:
        if (
            row["numtrades"] is not None
            and row["numtrades"] > 0
            and any(
                row[field] is None
                for field in ("open", "high", "low", "close")
            )
        ):
            incomplete_active.append(row)

    write_csv(
        OUTPUT_DIR / "active_trades_incomplete_ohlc.csv",
        incomplete_active,
        [
            "secid", "boardid", "tradedate",
            "numtrades", "volume", "value",
            "open", "high", "low", "close", "waprice",
        ],
    )

    # ---------------------------------------------------------
    # 5. Summary
    # ---------------------------------------------------------

    summary = {
        "period": [START_DATE, END_DATE],
        "rows": len(rows),
        "cross_board_groups": len(cross_rows),
        "cross_board_activity": dict(cross_summary),
        "no_trade_patterns": dict(no_trade_patterns),
        "no_trade_positive_close_rows": len(no_trade_positive_close),
        "large_changes_ge_50pct": len(large_changes),
        "active_trades_with_incomplete_ohlc": len(incomplete_active),
        "database_modified": False,
        "cleaning_performed": False,
    }

    with (OUTPUT_DIR / "summary.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\nCROSS-BOARD ACTIVITY")
    for key, count in cross_summary.most_common():
        print(f"{key}: {count:,}")

    print("\nNO-TRADE OHLC PATTERNS")
    for key, count in no_trade_patterns.most_common():
        print(f"{key}: {count:,}")

    print(
        "\nNo-trade positive CLOSE:",
        len(no_trade_positive_close),
    )
    print(
        "Large changes >= 50%:",
        len(large_changes),
    )
    print(
        "Active trades with incomplete OHLC:",
        len(incomplete_active),
    )

    print("\nReports:")
    for filename in (
        "summary.json",
        "cross_board_activity.csv",
        "no_trade_positive_close.csv",
        "large_price_changes.csv",
        "active_trades_incomplete_ohlc.csv",
    ):
        print(f"  {OUTPUT_DIR / filename}")

    print("\nDatabase modified: NO")
    print("Cleaning performed: NO")
    print("FORENSIC STAGE 2 COMPLETE")


if __name__ == "__main__":
    main()
