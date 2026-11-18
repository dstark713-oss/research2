
# Path: scripts/forensic_stage3.py
# Description: Builds detailed price and trading-activity context for large price changes from forensic_stage2.

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

DB_PATH = ROOT / "data" / "market_data" / "moex_daily_raw.sqlite"
INPUT_PATH = ROOT / "data" / "reports" / "forensic_stage2" / "large_price_changes.csv"
OUTPUT_DIR = ROOT / "data" / "reports" / "forensic_stage3"

EXCLUDED_TICKERS = {
    "GAZT": "Excluded by manual review: non-representative/noisy instrument",
    "GAZS": "Excluded by manual review: non-representative/noisy instrument",
    "GAZC": "Excluded by manual review: non-representative/noisy instrument",
}

ACTIVE_CONTEXT_BEFORE = 5
ACTIVE_CONTEXT_AFTER = 5

SOURCE_TABLE = "daily_bars_raw"

SELECT_COLUMNS = [
    "id",
    "secid",
    "boardid",
    "tradedate",
    "tradingsession",
    "numtrades",
    "volume",
    "value",
    "open",
    "low",
    "high",
    "close",
    "legalcloseprice",
    "waprice",
    "marketprice2",
    "marketprice3",
    "admittedquote",
    "admittedvalue",
    "shortname",
]


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def read_events(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Stage 2 input not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header.")

        normalized = {
            name.strip().lower(): name
            for name in reader.fieldnames
            if name
        }

        required = {
            "secid",
            "boardid",
            "previous_date",
            "current_date",
            "previous_close",
            "current_close",
            "change_pct",
        }
        missing = required - set(normalized)
        if missing:
            raise ValueError(
                f"Missing columns in Stage 2 CSV: {sorted(missing)}"
            )

        events = []
        for row in reader:
            event = {
                key: (row.get(original) or "").strip()
                for key, original in normalized.items()
            }
            event["secid"] = event["secid"].upper()
            event["boardid"] = event["boardid"].upper()
            events.append(event)

    return events


def get_table_columns(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        f'PRAGMA table_info("{SOURCE_TABLE}")'
    ).fetchall()

    if not rows:
        raise RuntimeError(f"SQLite table not found: {SOURCE_TABLE}")

    return {
        row[1].lower(): row[1]
        for row in rows
    }


def load_security_rows(
    connection: sqlite3.Connection,
    columns: dict[str, str],
    secid: str,
) -> list[dict[str, Any]]:
    selected = [
        name for name in SELECT_COLUMNS
        if name.lower() in columns
    ]

    required = {"secid", "boardid", "tradedate", "numtrades", "close"}
    missing = required - set(selected)
    if missing:
        raise RuntimeError(
            f"Required columns missing from {SOURCE_TABLE}: {sorted(missing)}"
        )

    select_sql = ", ".join(
        f'"{columns[name.lower()]}" AS "{name}"'
        for name in selected
    )

    sql = f"""
        SELECT {select_sql}
        FROM "{SOURCE_TABLE}"
        WHERE UPPER(secid) = ?
        ORDER BY tradedate, boardid
    """

    rows = connection.execute(sql, (secid,)).fetchall()
    return [dict(row) for row in rows]


def is_active(row: dict[str, Any]) -> bool:
    trades = to_int(row.get("numtrades"))
    close = to_float(row.get("close"))
    return trades is not None and trades > 0 and close is not None and close > 0


def date_diff_days(first: str, second: str) -> int | None:
    try:
        return (date.fromisoformat(second) - date.fromisoformat(first)).days
    except (TypeError, ValueError):
        return None


def build_event_context(
    event: dict[str, str],
    all_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    secid = event["secid"]
    boardid = event["boardid"]
    previous_date = event["previous_date"]
    current_date = event["current_date"]

    board_rows = [
        row for row in all_rows
        if str(row.get("boardid", "")).upper() == boardid
    ]
    board_rows.sort(key=lambda row: str(row.get("tradedate", "")))

    active_rows = [row for row in board_rows if is_active(row)]
    active_dates = [
        str(row.get("tradedate", ""))
        for row in active_rows
    ]

    try:
        previous_index = active_dates.index(previous_date)
        current_index = active_dates.index(current_date)
    except ValueError:
        return [], {
            "event_id": "",
            "secid": secid,
            "boardid": boardid,
            "previous_date": previous_date,
            "current_date": current_date,
            "status": "EVENT_OBSERVATIONS_NOT_FOUND",
        }

    if current_index <= previous_index:
        return [], {
            "event_id": "",
            "secid": secid,
            "boardid": boardid,
            "previous_date": previous_date,
            "current_date": current_date,
            "status": "INVALID_EVENT_ORDER",
        }

    start_index = max(0, previous_index - ACTIVE_CONTEXT_BEFORE)
    end_index = min(
        len(active_rows) - 1,
        current_index + ACTIVE_CONTEXT_AFTER,
    )

    context_start = str(active_rows[start_index]["tradedate"])
    context_end = str(active_rows[end_index]["tradedate"])

    event_id = (
        f"{secid}_{boardid}_{previous_date}_{current_date}"
    )

    active_lookup: dict[str, dict[str, Any]] = {}
    active_return_lookup: dict[str, float | None] = {}

    for index, row in enumerate(active_rows):
        tradedate = str(row["tradedate"])
        active_lookup[tradedate] = row

        if index == 0:
            active_return_lookup[tradedate] = None
        else:
            previous_close = to_float(active_rows[index - 1].get("close"))
            current_close = to_float(row.get("close"))

            if (
                previous_close is not None
                and current_close is not None
                and previous_close > 0
            ):
                active_return_lookup[tradedate] = (
                    current_close / previous_close - 1
                ) * 100
            else:
                active_return_lookup[tradedate] = None

    context_rows = []
    for row in all_rows:
        tradedate = str(row.get("tradedate", ""))
        if not context_start <= tradedate <= context_end:
            continue

        row_board = str(row.get("boardid", "")).upper()
        role = "CONTEXT"

        if row_board == boardid and tradedate == previous_date:
            role = "PREVIOUS_EVENT_OBSERVATION"
        elif row_board == boardid and tradedate == current_date:
            role = "CURRENT_EVENT_OBSERVATION"
        elif row_board != boardid:
            role = "OTHER_BOARD"

        close = to_float(row.get("close"))
        waprice = to_float(row.get("waprice"))
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        open_price = to_float(row.get("open"))

        close_vs_waprice_pct = None
        if (
            close is not None
            and waprice is not None
            and waprice > 0
        ):
            close_vs_waprice_pct = (close / waprice - 1) * 100

        intraday_range_pct = None
        if (
            high is not None
            and low is not None
            and low > 0
        ):
            intraday_range_pct = (high / low - 1) * 100

        ohlc_status = "NOT_CHECKABLE"
        if all(value is not None for value in (open_price, high, low, close)):
            if high < low:
                ohlc_status = "HIGH_BELOW_LOW"
            elif high < max(open_price, close):
                ohlc_status = "HIGH_BELOW_OPEN_OR_CLOSE"
            elif low > min(open_price, close):
                ohlc_status = "LOW_ABOVE_OPEN_OR_CLOSE"
            else:
                ohlc_status = "OK"

        output = dict(row)
        output.update({
            "event_id": event_id,
            "event_role": role,
            "is_target_board": row_board == boardid,
            "days_from_previous_event": date_diff_days(
                previous_date, tradedate
            ),
            "days_to_current_event": date_diff_days(
                tradedate, current_date
            ),
            "active_observation_return_pct": (
                active_return_lookup.get(tradedate)
                if row_board == boardid
                else None
            ),
            "close_vs_waprice_pct": close_vs_waprice_pct,
            "intraday_range_pct": intraday_range_pct,
            "ohlc_status": ohlc_status,
        })
        context_rows.append(output)

    metadata = {
        "event_id": event_id,
        "secid": secid,
        "boardid": boardid,
        "previous_date": previous_date,
        "current_date": current_date,
        "previous_close": to_float(event.get("previous_close")),
        "current_close": to_float(event.get("current_close")),
        "reported_change_pct": to_float(event.get("change_pct")),
        "calendar_gap_days": date_diff_days(
            previous_date, current_date
        ),
        "previous_numtrades": to_int(event.get("previous_numtrades")),
        "current_numtrades": to_int(event.get("current_numtrades")),
        "context_start": context_start,
        "context_end": context_end,
        "active_observations_in_context": end_index - start_index + 1,
        "status": "READY_FOR_MANUAL_REVIEW",
    }

    return context_rows, metadata


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events = read_events(INPUT_PATH)

    excluded_events = []
    review_events = []

    for event in events:
        ticker = event["secid"]
        if ticker in EXCLUDED_TICKERS:
            excluded_events.append(event)
        else:
            review_events.append(event)

    connection = sqlite3.connect(
        f"file:{DB_PATH.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    try:
        columns = get_table_columns(connection)

        security_cache: dict[str, list[dict[str, Any]]] = {}
        context_output: list[dict[str, Any]] = []
        review_queue: list[dict[str, Any]] = []
        failed_events: list[dict[str, Any]] = []

        for event in review_events:
            secid = event["secid"]

            if secid not in security_cache:
                security_cache[secid] = load_security_rows(
                    connection,
                    columns,
                    secid,
                )

            context, metadata = build_event_context(
                event,
                security_cache[secid],
            )

            if metadata["status"] != "READY_FOR_MANUAL_REVIEW":
                failed_events.append(metadata)
                continue

            context_output.extend(context)
            review_queue.append(metadata)

    finally:
        connection.close()

    excluded_tickers = [
        {
            "secid": ticker,
            "decision": "EXCLUDE_FROM_RESEARCH_UNIVERSE",
            "reason": reason,
            "raw_data_action": "KEEP_RAW_RECORDS_UNCHANGED",
        }
        for ticker, reason in sorted(EXCLUDED_TICKERS.items())
    ]

    write_csv(
        OUTPUT_DIR / "event_context.csv",
        context_output,
    )
    write_csv(
        OUTPUT_DIR / "review_queue.csv",
        review_queue,
    )
    write_csv(
        OUTPUT_DIR / "excluded_tickers.csv",
        excluded_tickers,
    )
    write_csv(
        OUTPUT_DIR / "events_not_found.csv",
        failed_events,
    )

    role_counts = Counter(
        row["event_role"] for row in context_output
    )
    board_counts = Counter(
        str(row.get("boardid", "")) for row in context_output
    )

    summary = {
        "source_database": str(DB_PATH),
        "source_events": len(events),
        "events_excluded_by_ticker": len(excluded_events),
        "events_for_review": len(review_events),
        "events_ready_for_review": len(review_queue),
        "events_not_found_or_invalid": len(failed_events),
        "excluded_tickers": sorted(EXCLUDED_TICKERS),
        "context_rows": len(context_output),
        "context_rows_by_role": dict(role_counts),
        "context_rows_by_board": dict(board_counts),
        "active_context_before": ACTIVE_CONTEXT_BEFORE,
        "active_context_after": ACTIVE_CONTEXT_AFTER,
        "raw_database_modified": False,
        "outputs": [
            "event_context.csv",
            "review_queue.csv",
            "excluded_tickers.csv",
            "events_not_found.csv",
            "summary.json",
        ],
    }

    with (OUTPUT_DIR / "summary.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nOutput directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
