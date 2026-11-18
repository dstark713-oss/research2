
# Path: scripts/forensic_stage3_1.py
# Description: Builds independent active-trading windows around both endpoints of every large price change.

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

DB_PATH = ROOT / "data" / "market_data" / "moex_daily_raw.sqlite"
INPUT_PATH = ROOT / "data" / "reports" / "forensic_stage2" / "large_price_changes.csv"
OUTPUT_DIR = ROOT / "data" / "reports" / "forensic_stage3_1"

EXCLUDED_TICKERS = {
    "GAZT": "Excluded by manual review",
    "GAZS": "Excluded by manual review",
    "GAZC": "Excluded by manual review",
}

ACTIVE_OBSERVATIONS_BEFORE = 5
ACTIVE_OBSERVATIONS_AFTER = 5

# Diagnostic candidates only. Never treated as proof of a corporate action.
SCALE_FACTORS = [
    2, 3, 4, 5, 8, 10, 20, 25, 50, 100, 500, 1000, 5000
]

SOURCE_TABLE = "daily_bars_raw"

COLUMNS_TO_LOAD = [
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
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def read_events() -> list[dict[str, str]]:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(INPUT_PATH)

    with INPUT_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
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
                f"Missing input columns: {sorted(missing)}"
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


def get_columns(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    rows = connection.execute(
        f'PRAGMA table_info("{SOURCE_TABLE}")'
    ).fetchall()

    if not rows:
        raise RuntimeError(
            f"Table not found: {SOURCE_TABLE}"
        )

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
        name for name in COLUMNS_TO_LOAD
        if name.lower() in columns
    ]

    required = {
        "secid", "boardid", "tradedate",
        "numtrades", "close",
    }

    missing = required - {name.lower() for name in selected}
    if missing:
        raise RuntimeError(
            f"Required DB columns missing: {sorted(missing)}"
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

    return (
        trades is not None
        and trades > 0
        and close is not None
        and close > 0
    )


def nearest_scale_factor(
    previous_close: float,
    current_close: float,
) -> dict[str, Any]:
    ratio = current_close / previous_close

    if ratio >= 1:
        implied_factor = ratio
        direction = "PRICE_MULTIPLIED"
    else:
        implied_factor = 1 / ratio
        direction = "PRICE_DIVIDED"

    factor = min(
        SCALE_FACTORS,
        key=lambda candidate: abs(
            math.log(implied_factor / candidate)
        ),
    )

    residual_pct = (
        implied_factor / factor - 1
    ) * 100

    if direction == "PRICE_MULTIPLIED":
        adjusted_change_pct = (
            ratio / factor - 1
        ) * 100
    else:
        adjusted_change_pct = (
            ratio * factor - 1
        ) * 100

    if abs(residual_pct) <= 2:
        fit = "CLOSE"
    elif abs(residual_pct) <= 10:
        fit = "APPROXIMATE"
    else:
        fit = "WEAK"

    return {
        "price_ratio": ratio,
        "scale_direction": direction,
        "implied_scale_factor": implied_factor,
        "nearest_scale_factor": factor,
        "factor_residual_pct": residual_pct,
        "candidate_adjusted_change_pct": adjusted_change_pct,
        "factor_fit": fit,
    }


def find_anchor(
    active_rows: list[dict[str, Any]],
    target_date: str,
) -> tuple[int | None, str]:
    matches = [
        index
        for index, row in enumerate(active_rows)
        if str(row["tradedate"]) == target_date
    ]

    if len(matches) == 0:
        return None, "ANCHOR_NOT_FOUND"

    if len(matches) > 1:
        return None, "DUPLICATE_ACTIVE_ANCHOR_DATE"

    return matches[0], "OK"


def build_endpoint_context(
    event_id: str,
    endpoint_name: str,
    anchor_index: int,
    active_rows: list[dict[str, Any]],
    anchor_date: str,
) -> list[dict[str, Any]]:
    start = max(
        0,
        anchor_index - ACTIVE_OBSERVATIONS_BEFORE,
    )
    end = min(
        len(active_rows),
        anchor_index + ACTIVE_OBSERVATIONS_AFTER + 1,
    )

    output = []

    for index in range(start, end):
        row = dict(active_rows[index])

        relative_index = index - anchor_index

        if relative_index < 0:
            position = "BEFORE"
        elif relative_index > 0:
            position = "AFTER"
        else:
            position = "ANCHOR"

        previous_active = (
            active_rows[index - 1]
            if index > 0
            else None
        )

        current_close = to_float(row.get("close"))
        previous_close = (
            to_float(previous_active.get("close"))
            if previous_active is not None
            else None
        )

        active_return_pct = None
        days_since_previous = None

        if (
            current_close is not None
            and previous_close is not None
            and previous_close > 0
        ):
            active_return_pct = (
                current_close / previous_close - 1
            ) * 100

            days_since_previous = (
                (
                    __import__("datetime").date.fromisoformat(
                        str(row["tradedate"])
                    )
                    -
                    __import__("datetime").date.fromisoformat(
                        str(previous_active["tradedate"])
                    )
                ).days
            )

        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        open_price = to_float(row.get("open"))

        ohlc_status = "NOT_CHECKABLE"

        if all(
            value is not None
            for value in (
                open_price,
                high,
                low,
                current_close,
            )
        ):
            if high < low:
                ohlc_status = "HIGH_BELOW_LOW"
            elif high < max(open_price, current_close):
                ohlc_status = "HIGH_BELOW_OPEN_OR_CLOSE"
            elif low > min(open_price, current_close):
                ohlc_status = "LOW_ABOVE_OPEN_OR_CLOSE"
            else:
                ohlc_status = "OK"

        row.update({
            "event_id": event_id,
            "endpoint": endpoint_name,
            "anchor_date": anchor_date,
            "relative_active_index": relative_index,
            "position": position,
            "active_return_pct": active_return_pct,
            "days_since_previous_active": days_since_previous,
            "ohlc_status": ohlc_status,
        })

        output.append(row)

    return output


def build_event_summary(
    event: dict[str, str],
    all_rows: list[dict[str, Any]],
    active_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    secid = event["secid"]
    boardid = event["boardid"]
    previous_date = event["previous_date"]
    current_date = event["current_date"]

    event_id = (
        f"{secid}_{boardid}_{previous_date}_{current_date}"
    )

    board_rows = [
        row for row in all_rows
        if str(row.get("boardid", "")).upper() == boardid
    ]

    active_board_rows = [
        row for row in active_rows
        if str(row.get("boardid", "")).upper() == boardid
    ]

    previous_index, previous_status = find_anchor(
        active_board_rows,
        previous_date,
    )
    current_index, current_status = find_anchor(
        active_board_rows,
        current_date,
    )

    if previous_status != "OK" or current_status != "OK":
        return {
            "event_id": event_id,
            "secid": secid,
            "boardid": boardid,
            "previous_date": previous_date,
            "current_date": current_date,
            "status": (
                previous_status
                if previous_status != "OK"
                else current_status
            ),
        }, []

    if current_index <= previous_index:
        return {
            "event_id": event_id,
            "secid": secid,
            "boardid": boardid,
            "previous_date": previous_date,
            "current_date": current_date,
            "status": "INVALID_EVENT_ORDER",
        }, []

    previous_row = active_board_rows[previous_index]
    current_row = active_board_rows[current_index]

    previous_close = to_float(previous_row.get("close"))
    current_close = to_float(current_row.get("close"))

    if (
        previous_close is None
        or current_close is None
        or previous_close <= 0
        or current_close <= 0
    ):
        return {
            "event_id": event_id,
            "secid": secid,
            "boardid": boardid,
            "previous_date": previous_date,
            "current_date": current_date,
            "status": "INVALID_ANCHOR_PRICE",
        }, []

    between_active = (
        current_index - previous_index - 1
    )

    between_rows = [
        row for row in board_rows
        if previous_date < str(row["tradedate"]) < current_date
    ]

    zero_trade_rows = sum(
        1
        for row in between_rows
        if (to_int(row.get("numtrades")) or 0) <= 0
    )

    factor_info = nearest_scale_factor(
        previous_close,
        current_close,
    )

    calendar_gap = (
        __import__("datetime").date.fromisoformat(current_date)
        -
        __import__("datetime").date.fromisoformat(previous_date)
    ).days

    summary = {
        "event_id": event_id,
        "secid": secid,
        "boardid": boardid,
        "previous_date": previous_date,
        "current_date": current_date,
        "previous_close": previous_close,
        "current_close": current_close,
        "reported_change_pct": to_float(event.get("change_pct")),
        "calendar_gap_days": calendar_gap,
        "previous_numtrades": to_int(previous_row.get("numtrades")),
        "current_numtrades": to_int(current_row.get("numtrades")),
        "previous_volume": to_float(previous_row.get("volume")),
        "current_volume": to_float(current_row.get("volume")),
        "previous_value": to_float(previous_row.get("value")),
        "current_value": to_float(current_row.get("value")),
        "intervening_active_observations": between_active,
        "intervening_zero_trade_rows": zero_trade_rows,
        "intervening_total_rows": len(between_rows),
        "status": "READY_FOR_MANUAL_REVIEW",
        **factor_info,
    }

    context = []
    context.extend(
        build_endpoint_context(
            event_id,
            "PREVIOUS_ENDPOINT",
            previous_index,
            active_board_rows,
            previous_date,
        )
    )
    context.extend(
        build_endpoint_context(
            event_id,
            "CURRENT_ENDPOINT",
            current_index,
            active_board_rows,
            current_date,
        )
    )

    return summary, context


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
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

    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events = read_events()

    connection = sqlite3.connect(
        f"file:{DB_PATH.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")

    event_summaries = []
    context_rows = []
    excluded_rows = []
    failed_rows = []

    try:
        columns = get_columns(connection)
        cache: dict[str, list[dict[str, Any]]] = {}

        for event in events:
            secid = event["secid"]

            if secid in EXCLUDED_TICKERS:
                excluded_rows.append({
                    "secid": secid,
                    "event_id": (
                        f"{secid}_{event['boardid']}_"
                        f"{event['previous_date']}_"
                        f"{event['current_date']}"
                    ),
                    "decision": "EXCLUDE_FROM_RESEARCH_UNIVERSE",
                    "reason": EXCLUDED_TICKERS[secid],
                    "raw_data_action": "KEEP_RAW_RECORDS_UNCHANGED",
                })
                continue

            if secid not in cache:
                cache[secid] = load_security_rows(
                    connection,
                    columns,
                    secid,
                )

            all_rows = cache[secid]

            active_rows = [
                row for row in all_rows
                if is_active(row)
            ]

            summary, context = build_event_summary(
                event,
                all_rows,
                active_rows,
            )

            if summary["status"] != "READY_FOR_MANUAL_REVIEW":
                failed_rows.append(summary)
                continue

            event_summaries.append(summary)
            context_rows.extend(context)

    finally:
        connection.close()

    write_csv(
        OUTPUT_DIR / "event_summary.csv",
        event_summaries,
    )
    write_csv(
        OUTPUT_DIR / "endpoint_context.csv",
        context_rows,
    )
    write_csv(
        OUTPUT_DIR / "excluded_tickers.csv",
        excluded_rows,
    )
    write_csv(
        OUTPUT_DIR / "events_not_ready.csv",
        failed_rows,
    )

    fit_counts = Counter(
        row.get("factor_fit")
        for row in event_summaries
    )

    summary = {
        "source_events": len(events),
        "events_excluded_by_ticker": len(excluded_rows),
        "events_ready_for_review": len(event_summaries),
        "events_not_ready": len(failed_rows),
        "endpoint_context_rows": len(context_rows),
        "context_design": (
            "Independent active-observation windows around "
            "previous and current event endpoints"
        ),
        "active_observations_before": ACTIVE_OBSERVATIONS_BEFORE,
        "active_observations_after": ACTIVE_OBSERVATIONS_AFTER,
        "scale_factor_fit_counts": dict(fit_counts),
        "scale_factors_are_diagnostic_only": True,
        "raw_database_modified": False,
        "outputs": [
            "event_summary.csv",
            "endpoint_context.csv",
            "excluded_tickers.csv",
            "events_not_ready.csv",
            "summary.json",
        ],
    }

    with (OUTPUT_DIR / "summary.json").open(
        "w", encoding="utf-8"
    ) as file:
        json.dump(
            summary,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nOutput directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
