
# Path: scripts/build_historical_universe.py
# Description: Builds a historical candidate universe of MOEX common shares using ISS listing data and security metadata.

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any


BASE_URL = "https://iss.moex.com/iss"
DEFAULT_FROM = "1997-03-24"
DEFAULT_PAGE_SIZE = 100
DEFAULT_DELAY = 0.15
MAX_RETRIES = 5

COMMON_SHARE_TYPE = "common_share"

LISTING_COLUMNS = [
    "SECID",
    "SHORTNAME",
    "NAME",
    "BOARDID",
    "decimals",
    "history_from",
    "history_till",
]

LOG = logging.getLogger("historical_universe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build historical MOEX common-share universe."
    )
    parser.add_argument(
        "--from-date",
        default=DEFAULT_FROM,
        help=f"Start date for listing history. Default: {DEFAULT_FROM}",
    )
    parser.add_argument(
        "--till-date",
        default=date.today().isoformat(),
        help="End date for listing history. Default: today.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/interim/historical_universe",
        help="Directory for output files.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Listing pagination step. Verified default: 100.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Delay between successful API requests in seconds.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help="Maximum retries per API request.",
    )
    parser.add_argument(
        "--refresh-listing",
        action="store_true",
        help="Ignore cached listing pages and download them again.",
    )
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="Ignore cached security metadata and download it again.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def validate_dates(from_date: str, till_date: str) -> None:
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(till_date)

    if start > end:
        raise ValueError("--from-date must not be later than --till-date")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
    )


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def request_json(
    url: str,
    max_retries: int,
    delay: float,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "Research2-HistoricalUniverse/1.0",
        "Accept": "application/json",
    }

    for attempt in range(max_retries):
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Unexpected HTTP status {response.status}: {url}"
                    )

                payload = response.read().decode("utf-8")
                result = json.loads(payload)

                if not isinstance(result, dict):
                    raise ValueError("ISS response is not a JSON object")

                time.sleep(delay)
                return result

        except urllib.error.HTTPError as exc:
            retryable = exc.code in (408, 425, 429, 500, 502, 503, 504)

            if not retryable or attempt == max_retries - 1:
                raise

            wait = min(60.0, (2**attempt) + random.uniform(0, 1))
            LOG.warning(
                "HTTP %s. Retry %s/%s in %.1fs: %s",
                exc.code,
                attempt + 1,
                max_retries,
                wait,
                url,
            )
            time.sleep(wait)

        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            json.JSONDecodeError,
        ) as exc:
            if attempt == max_retries - 1:
                raise

            wait = min(60.0, (2**attempt) + random.uniform(0, 1))
            LOG.warning(
                "Request failed (%s). Retry %s/%s in %.1fs: %s",
                exc,
                attempt + 1,
                max_retries,
                wait,
                url,
            )
            time.sleep(wait)

    raise RuntimeError(f"Request failed after retries: {url}")


def build_listing_url(
    from_date: str,
    till_date: str,
    start: int,
) -> str:
    params = {
        "from": from_date,
        "till": till_date,
        "iss.meta": "off",
        "start": str(start),
    }

    return (
        f"{BASE_URL}/history/engines/stock/markets/shares/listing.json?"
        f"{urllib.parse.urlencode(params)}"
    )


def parse_iss_block(
    payload: dict[str, Any],
    block_name: str,
) -> tuple[list[str], list[list[Any]]]:
    block = payload.get(block_name)

    if not isinstance(block, dict):
        raise ValueError(f"Missing ISS block: {block_name}")

    columns = block.get("columns", [])
    rows = block.get("data", [])

    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError(f"Invalid ISS block structure: {block_name}")

    return columns, rows


def listing_page_to_dicts(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    columns, rows = parse_iss_block(payload, "securities")

    if not columns:
        raise ValueError("Listing response contains no columns")

    result = []

    for row in rows:
        if len(row) != len(columns):
            LOG.warning(
                "Skipping malformed listing row: %s values, %s columns",
                len(row),
                len(columns),
            )
            continue

        result.append(dict(zip(columns, row)))

    return result


def download_listing(
    output_dir: Path,
    from_date: str,
    till_date: str,
    page_size: int,
    delay: float,
    max_retries: int,
    refresh: bool,
) -> list[dict[str, Any]]:
    pages_dir = output_dir / "listing_pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    start = 0
    page_number = 0

    while True:
        cache_path = pages_dir / f"page_{start:09d}.json"

        if cache_path.exists() and not refresh:
            payload = read_json(cache_path)
            LOG.info("Loaded cached listing page: start=%s", start)
        else:
            url = build_listing_url(from_date, till_date, start)
            LOG.info("Downloading listing page: start=%s", start)

            payload = request_json(
                url=url,
                max_retries=max_retries,
                delay=delay,
            )
            atomic_write_json(cache_path, payload)

        rows = listing_page_to_dicts(payload)
        page_count = len(rows)

        LOG.info(
            "Listing page %s: start=%s, rows=%s",
            page_number + 1,
            start,
            page_count,
        )

        if page_count == 0:
            break

        all_rows.extend(rows)
        page_number += 1

        # Offset pagination: advance by the actual number of returned rows.
        start += page_count

        # A short page means the final page has been reached.
        if page_count < page_size:
            break

    if not all_rows:
        raise RuntimeError("MOEX listing returned no rows")

    LOG.info(
        "Listing download complete: %s board-level rows",
        len(all_rows),
    )

    return all_rows


def get_description_metadata(
    secid: str,
    cache_dir: Path,
    delay: float,
    max_retries: int,
    refresh: bool,
) -> dict[str, Any]:
    cache_path = cache_dir / f"{secid}.json"

    if cache_path.exists() and not refresh:
        payload = read_json(cache_path)
    else:
        encoded_secid = urllib.parse.quote(secid, safe="")
        params = urllib.parse.urlencode(
            {
                "iss.meta": "off",
                "iss.only": "description",
            }
        )
        url = (
            f"{BASE_URL}/securities/{encoded_secid}.json?{params}"
        )

        payload = request_json(
            url=url,
            max_retries=max_retries,
            delay=delay,
        )
        atomic_write_json(cache_path, payload)

    columns, rows = parse_iss_block(payload, "description")

    metadata: dict[str, Any] = {}

    for row in rows:
        if len(row) != len(columns):
            continue

        item = dict(zip(columns, row))
        name = item.get("name")
        value = item.get("value")

        if name is not None:
            metadata[str(name)] = value

    return metadata


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if fieldnames is None:
        fieldnames = []

        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                    for key, value in row.items()
                }
            )

    temp_path.replace(path)


def normalize_listing_row(
    row: dict[str, Any],
) -> dict[str, Any]:
    return {
        column: row.get(column)
        for column in LISTING_COLUMNS
    }


def aggregate_securities(
    listing_rows: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
]:
    by_secid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized_rows = []

    for original_row in listing_rows:
        row = normalize_listing_row(original_row)
        secid = row.get("SECID")

        if secid is None or str(secid).strip() == "":
            LOG.warning("Listing row without SECID: %s", original_row)
            continue

        secid = str(secid).strip()
        row["SECID"] = secid

        by_secid[secid].append(row)
        normalized_rows.append(row)

    return by_secid, normalized_rows


def classify_security(
    metadata: dict[str, Any] | None,
    error: str | None = None,
) -> tuple[str, str]:
    if error:
        return "quarantine", f"metadata_request_failed: {error}"

    if not metadata:
        return "quarantine", "empty_metadata"

    security_type = metadata.get("TYPE")

    if security_type is None or str(security_type).strip() == "":
        return "quarantine", "missing_TYPE"

    security_type = str(security_type).strip().lower()

    if security_type == COMMON_SHARE_TYPE:
        return "included", "TYPE=common_share"

    if security_type == "preferred_share":
        return "excluded", "TYPE=preferred_share"

    return "excluded", f"non_common_security_type: {security_type}"


def aggregate_board_history(
    board_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    boards = sorted(
        {
            str(row["BOARDID"])
            for row in board_rows
            if row.get("BOARDID") is not None
        }
    )

    history_from_values = [
        str(row["history_from"])
        for row in board_rows
        if row.get("history_from")
    ]

    history_till_values = [
        str(row["history_till"])
        for row in board_rows
        if row.get("history_till")
    ]

    return {
        "BOARD_COUNT": len(boards),
        "BOARDS": boards,
        "HISTORY_DATA_FROM_MIN": (
            min(history_from_values) if history_from_values else None
        ),
        "HISTORY_DATA_TILL_MAX": (
            max(history_till_values) if history_till_values else None
        ),
    }


def build_universe(
    listing_rows: list[dict[str, Any]],
    output_dir: Path,
    delay: float,
    max_retries: int,
    refresh_metadata: bool,
) -> dict[str, Any]:
    metadata_cache_dir = output_dir / "metadata_cache"
    metadata_cache_dir.mkdir(parents=True, exist_ok=True)

    securities, normalized_listing = aggregate_securities(listing_rows)

    secids = sorted(securities)

    LOG.info("Unique SECIDs: %s", len(secids))

    master_rows = []
    common_rows = []
    excluded_rows = []
    quarantine_rows = []

    for index, secid in enumerate(secids, start=1):
        LOG.info(
            "Metadata %s/%s: %s",
            index,
            len(secids),
            secid,
        )

        metadata = None
        error = None

        try:
            metadata = get_description_metadata(
                secid=secid,
                cache_dir=metadata_cache_dir,
                delay=delay,
                max_retries=max_retries,
                refresh=refresh_metadata,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            LOG.error("Metadata failed for %s: %s", secid, error)

        status, reason = classify_security(metadata, error)

        board_info = aggregate_board_history(securities[secid])

        master_row = {
            "SECID": secid,
            "STATUS": status,
            "CLASSIFICATION_REASON": reason,
            "TYPE": metadata.get("TYPE") if metadata else None,
            "GROUP": metadata.get("GROUP") if metadata else None,
            "TYPENAME": metadata.get("TYPENAME") if metadata else None,
            "ISIN": metadata.get("ISIN") if metadata else None,
            "SHORTNAME": metadata.get("SHORTNAME") if metadata else None,
            "NAME": metadata.get("NAME") if metadata else None,
            "BOARD_COUNT": board_info["BOARD_COUNT"],
            "BOARDS": board_info["BOARDS"],
            "HISTORY_DATA_FROM_MIN": board_info[
                "HISTORY_DATA_FROM_MIN"
            ],
            "HISTORY_DATA_TILL_MAX": board_info[
                "HISTORY_DATA_TILL_MAX"
            ],
            "METADATA_ERROR": error,
        }

        if metadata:
            master_row["METADATA_JSON"] = metadata

        master_rows.append(master_row)

        if status == "included":
            common_rows.append(master_row)
        elif status == "excluded":
            excluded_rows.append(master_row)
        else:
            quarantine_rows.append(master_row)

        # Periodically persist progress so a long run remains auditable.
        if index % 50 == 0 or index == len(secids):
            write_csv(
                output_dir / "security_master_partial.csv",
                master_rows,
            )
            LOG.info(
                "Metadata progress saved: %s/%s",
                index,
                len(secids),
            )

    return {
        "listing_rows": normalized_listing,
        "security_master": master_rows,
        "common_shares": common_rows,
        "exclusions": excluded_rows,
        "quarantine": quarantine_rows,
    }


def save_outputs(
    output_dir: Path,
    listing_rows: list[dict[str, Any]],
    results: dict[str, Any],
    from_date: str,
    till_date: str,
    page_size: int,
) -> None:
    write_csv(
        output_dir / "listing_raw.csv",
        listing_rows,
        LISTING_COLUMNS,
    )

    write_csv(
        output_dir / "security_master.csv",
        results["security_master"],
    )

    write_csv(
        output_dir / "universe_common_shares.csv",
        results["common_shares"],
    )

    write_csv(
        output_dir / "universe_exclusions.csv",
        results["exclusions"],
    )

    write_csv(
        output_dir / "universe_quarantine.csv",
        results["quarantine"],
    )

    # One row per SECID + BOARDID.
    # These are data-history boundaries, not legal listing intervals.
    write_csv(
        output_dir / "universe_board_history.csv",
        listing_rows,
        LISTING_COLUMNS,
    )

    type_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)

    for row in results["security_master"]:
        security_type = row.get("TYPE") or "UNKNOWN"
        status = row.get("STATUS") or "UNKNOWN"

        type_counts[str(security_type)] += 1
        status_counts[str(status)] += 1

    summary = {
        "source": (
            "MOEX ISS "
            "/history/engines/stock/markets/shares/listing"
        ),
        "metadata_source": "MOEX ISS /securities/{SECID}",
        "from_date": from_date,
        "till_date": till_date,
        "page_size_step": page_size,
        "listing_board_rows": len(listing_rows),
        "unique_secids": len(results["security_master"]),
        "common_shares_included": len(results["common_shares"]),
        "securities_excluded": len(results["exclusions"]),
        "securities_quarantined": len(results["quarantine"]),
        "status_counts": dict(status_counts),
        "type_counts": dict(type_counts),
        "classification_policy": {
            "included": "TYPE == common_share",
            "preferred_shares": "excluded",
            "other_types": "excluded",
            "missing_or_failed_metadata": "quarantine",
        },
        "important_limitations": [
            (
                "Listing history_from/history_till are data availability "
                "boundaries, not legal listing intervals."
            ),
            (
                "Listing catalog membership does not prove that a security "
                "was traded on every date in its history range."
            ),
            (
                "This stage does not download daily bars or establish "
                "daily trading activity."
            ),
            (
                "Historical identity changes and ticker reuse require "
                "separate investigation."
            ),
        ],
    }

    atomic_write_json(output_dir / "universe_summary.json", summary)


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    validate_dates(args.from_date, args.till_date)

    if args.page_size <= 0:
        raise ValueError("--page-size must be positive")

    if args.delay < 0:
        raise ValueError("--delay cannot be negative")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("MOEX historical universe build started")
    LOG.info("Date range: %s — %s", args.from_date, args.till_date)
    LOG.info("Output directory: %s", output_dir.resolve())

    listing_rows = download_listing(
        output_dir=output_dir,
        from_date=args.from_date,
        till_date=args.till_date,
        page_size=args.page_size,
        delay=args.delay,
        max_retries=args.max_retries,
        refresh=args.refresh_listing,
    )

    results = build_universe(
        listing_rows=listing_rows,
        output_dir=output_dir,
        delay=args.delay,
        max_retries=args.max_retries,
        refresh_metadata=args.refresh_metadata,
    )

    save_outputs(
        output_dir=output_dir,
        listing_rows=results["listing_rows"],
        results=results,
        from_date=args.from_date,
        till_date=args.till_date,
        page_size=args.page_size,
    )

    partial_path = output_dir / "security_master_partial.csv"
    if partial_path.exists():
        partial_path.unlink()

    LOG.info("Build completed successfully")
    LOG.info("Listing rows: %s", len(results["listing_rows"]))
    LOG.info("Unique securities: %s", len(results["security_master"]))
    LOG.info("Common shares: %s", len(results["common_shares"]))
    LOG.info("Excluded: %s", len(results["exclusions"]))
    LOG.info("Quarantined: %s", len(results["quarantine"]))
    LOG.info("Summary: %s", output_dir / "universe_summary.json")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user")
        raise SystemExit(130)
    except Exception:
        LOG.exception("Historical universe build failed")
        raise SystemExit(1)
