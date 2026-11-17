
# Path: scripts/audit_traded_universe.py
# Description: Audits MOEX common shares using only approved primary trading boards.

from __future__ import annotations

import argparse
import csv
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


BASE_URL = "https://iss.moex.com/iss"

# Only approved primary stock trading boards.
ALLOWED_BOARDS = {
    "TQBR",
    "EQBR",
}

LOG = logging.getLogger("traded_universe_audit")

HISTORY_COLUMNS = ["TRADEDATE", "NUMTRADES"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit actual MOEX common-share trading."
    )
    parser.add_argument(
        "--input-dir",
        default="data/interim/historical_universe",
    )
    parser.add_argument(
        "--output-dir",
        default="data/interim/traded_universe_audit",
    )
    parser.add_argument(
        "--from-date",
        default="1997-03-24",
    )
    parser.add_argument(
        "--till-date",
        default=date.today().isoformat(),
    )
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached audit responses.",
    )
    parser.add_argument(
        "--probe-secid",
        default="SBER",
    )
    parser.add_argument(
        "--probe-board",
        default="TQBR",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Required input file not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    fieldnames = list(rows[0].keys())
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def request_json(
    url: str,
    max_retries: int,
    delay: float,
) -> dict[str, Any]:
    headers = {
        "User-Agent": "Research2-TradedUniverseAudit/1.1",
        "Accept": "application/json",
    }

    for attempt in range(max_retries):
        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Unexpected HTTP status: {response.status}"
                    )

                payload = json.loads(
                    response.read().decode("utf-8")
                )

                if not isinstance(payload, dict):
                    raise ValueError("Invalid ISS JSON response")

                time.sleep(delay)
                return payload

        except urllib.error.HTTPError as exc:
            retryable = exc.code in (
                408, 425, 429, 500, 502, 503, 504
            )

            if not retryable or attempt == max_retries - 1:
                raise

            wait = min(60, 2**attempt)
            LOG.warning(
                "HTTP %s; retry in %ss: %s",
                exc.code,
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

            wait = min(60, 2**attempt)
            LOG.warning(
                "Request error %s; retry in %ss",
                exc,
                wait,
            )
            time.sleep(wait)

    raise RuntimeError(f"Request failed: {url}")


def build_history_url(
    secid: str,
    board: str,
    from_date: str,
    till_date: str,
) -> str:
    params = {
        "from": from_date,
        "till": till_date,
        "numtrades": "1",
        "limit": "1",
        "start": "0",
        "iss.meta": "off",
        "iss.only": "history",
        "history.columns": ",".join(HISTORY_COLUMNS),
    }

    return (
        f"{BASE_URL}/history/engines/stock/markets/shares/"
        f"boards/{urllib.parse.quote(board, safe='')}/"
        f"securities/{urllib.parse.quote(secid, safe='')}.json?"
        f"{urllib.parse.urlencode(params)}"
    )


def parse_history(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    block = payload.get("history", {})

    if not isinstance(block, dict):
        raise ValueError("Missing history block")

    columns = block.get("columns", [])
    rows = block.get("data", [])

    if not columns:
        raise ValueError("History response has no columns")

    result = []

    for row in rows:
        if len(row) != len(columns):
            continue

        result.append(dict(zip(columns, row)))

    return result


def find_positive_trade(
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for row in rows:
        value = row.get("NUMTRADES")

        if value is None:
            continue

        try:
            numtrades = float(value)
        except (TypeError, ValueError):
            continue

        if numtrades > 0:
            return {
                "trade_date": row.get("TRADEDATE"),
                "numtrades": numtrades,
            }

    return None


def probe_filter(args: argparse.Namespace) -> None:
    if args.probe_board not in ALLOWED_BOARDS:
        raise ValueError(
            f"Probe board {args.probe_board} is not in "
            f"ALLOWED_BOARDS={sorted(ALLOWED_BOARDS)}"
        )

    url = build_history_url(
        secid=args.probe_secid,
        board=args.probe_board,
        from_date="2026-01-01",
        till_date=args.till_date,
    )

    payload = request_json(
        url=url,
        max_retries=args.max_retries,
        delay=args.delay,
    )

    rows = parse_history(payload)
    trade = find_positive_trade(rows)

    if trade is None:
        raise RuntimeError(
            "NUMTRADES preflight failed. "
            f"No positive trade returned for "
            f"{args.probe_secid}/{args.probe_board}. "
            "Audit aborted."
        )

    LOG.info(
        "NUMTRADES preflight passed: %s/%s, date=%s",
        args.probe_secid,
        args.probe_board,
        trade["trade_date"],
    )


def audit_board(
    secid: str,
    board: str,
    from_date: str,
    till_date: str,
    cache_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if board not in ALLOWED_BOARDS:
        raise ValueError(f"Disallowed board: {board}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{secid}__{board}.json"

    if cache_path.exists() and not args.refresh:
        payload = json.loads(
            cache_path.read_text(encoding="utf-8")
        )
    else:
        url = build_history_url(
            secid=secid,
            board=board,
            from_date=from_date,
            till_date=till_date,
        )

        payload = request_json(
            url=url,
            max_retries=args.max_retries,
            delay=args.delay,
        )

        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    rows = parse_history(payload)
    trade = find_positive_trade(rows)

    return {
        "SECID": secid,
        "BOARDID": board,
        "STATUS": "traded" if trade else "no_trade",
        "FIRST_FOUND_TRADE_DATE": (
            trade["trade_date"] if trade else None
        ),
        "NUMTRADES_ON_FOUND_DATE": (
            trade["numtrades"] if trade else None
        ),
    }


def prepare_board_map(
    board_rows: list[dict[str, str]],
    candidate_secids: set[str],
) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in board_rows:
        secid = (row.get("SECID") or "").strip()
        board = (row.get("BOARDID") or "").strip()

        if secid not in candidate_secids:
            continue

        if board not in ALLOWED_BOARDS:
            continue

        result[secid].append(row)

    # Check TQBR first, then EQBR.
    for secid in result:
        result[secid].sort(
            key=lambda row: (
                row.get("BOARDID") != "TQBR",
                row.get("BOARDID") or "",
            )
        )

    return result


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "cache"

    common_rows = read_csv(
        input_dir / "universe_common_shares.csv"
    )
    board_rows = read_csv(
        input_dir / "universe_board_history.csv"
    )

    common_by_secid = {
        row["SECID"].strip(): row
        for row in common_rows
        if row.get("SECID", "").strip()
    }

    candidate_secids = set(common_by_secid)

    if not candidate_secids:
        raise RuntimeError("No common-share candidates found")

    board_map = prepare_board_map(
        board_rows=board_rows,
        candidate_secids=candidate_secids,
    )

    LOG.info("Allowed boards: %s", sorted(ALLOWED_BOARDS))
    LOG.info("Candidate common shares: %s", len(candidate_secids))
    LOG.info(
        "Securities with approved board records: %s",
        len(board_map),
    )

    probe_filter(args)

    audit_rows = []
    board_audit_rows = []
    counts = Counter()

    for index, secid in enumerate(
        sorted(candidate_secids),
        start=1,
    ):
        boards = board_map.get(secid, [])

        if not boards:
            audit_rows.append({
                "SECID": secid,
                "STATUS": "no_approved_board",
                "BOARDS_CHECKED": 0,
                "TRADED_BOARD": None,
                "FIRST_FOUND_TRADE_DATE": None,
                "ERROR": None,
            })
            counts["no_approved_board"] += 1
            continue

        traded_board = None
        first_trade_date = None
        errors = []
        checked_count = 0

        for board_row in boards:
            board = board_row["BOARDID"]

            board_from = board_row.get("history_from") or args.from_date
            board_till = board_row.get("history_till") or args.till_date

            board_from = max(board_from, args.from_date)
            board_till = min(board_till, args.till_date)

            if board_from > board_till:
                continue

            try:
                result = audit_board(
                    secid=secid,
                    board=board,
                    from_date=board_from,
                    till_date=board_till,
                    cache_dir=cache_dir,
                    args=args,
                )

                checked_count += 1
                board_audit_rows.append(result)

                if result["STATUS"] == "traded":
                    traded_board = board
                    first_trade_date = result["FIRST_FOUND_TRADE_DATE"]
                    break

            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                errors.append(f"{board}: {error_text}")

                LOG.warning(
                    "%s/%s audit failed: %s",
                    secid,
                    board,
                    error_text,
                )

        if traded_board:
            status = "traded"
            counts["traded"] += 1
        elif errors:
            status = "audit_error"
            counts["audit_error"] += 1
        else:
            status = "no_trade"
            counts["no_trade"] += 1

        audit_rows.append({
            "SECID": secid,
            "STATUS": status,
            "BOARDS_CHECKED": checked_count,
            "TRADED_BOARD": traded_board,
            "FIRST_FOUND_TRADE_DATE": first_trade_date,
            "ERROR": " | ".join(errors) if errors else None,
        })

        if index % 25 == 0 or index == len(candidate_secids):
            LOG.info(
                "Progress %s/%s | traded=%s | no_trade=%s | "
                "no_approved_board=%s | errors=%s",
                index,
                len(candidate_secids),
                counts["traded"],
                counts["no_trade"],
                counts["no_approved_board"],
                counts["audit_error"],
            )

            write_csv(
                output_dir / "audit_by_security_partial.csv",
                audit_rows,
            )

    audit_by_secid = {
        row["SECID"]: row
        for row in audit_rows
    }

    traded_universe = []

    for secid, original_row in common_by_secid.items():
        audit = audit_by_secid[secid]

        if audit["STATUS"] != "traded":
            continue

        output_row = dict(original_row)
        output_row["TRADED_BOARD"] = audit["TRADED_BOARD"]
        output_row["FIRST_FOUND_TRADE_DATE"] = (
            audit["FIRST_FOUND_TRADE_DATE"]
        )
        traded_universe.append(output_row)

    no_trade_rows = [
        row for row in audit_rows
        if row["STATUS"] in ("no_trade", "no_approved_board")
    ]

    error_rows = [
        row for row in audit_rows
        if row["STATUS"] == "audit_error"
    ]

    write_csv(output_dir / "audit_by_security.csv", audit_rows)
    write_csv(output_dir / "audit_by_board.csv", board_audit_rows)
    write_csv(
        output_dir / "universe_actually_traded.csv",
        traded_universe,
    )
    write_csv(output_dir / "securities_no_trade.csv", no_trade_rows)
    write_csv(output_dir / "securities_audit_errors.csv", error_rows)

    summary = {
        "source": "MOEX ISS historical security/board history",
        "filter": "NUMTRADES > 0",
        "allowed_boards": sorted(ALLOWED_BOARDS),
        "from_date": args.from_date,
        "till_date": args.till_date,
        "candidate_common_shares": len(candidate_secids),
        "securities_with_approved_board_records": len(board_map),
        "actually_traded": counts["traded"],
        "no_trade": counts["no_trade"],
        "no_approved_board": counts["no_approved_board"],
        "audit_errors": counts["audit_error"],
        "final_universe": len(traded_universe),
        "universe_file": "universe_actually_traded.csv",
        "policy": (
            "A common share is included only if at least one approved "
            "board (TQBR or EQBR) has a historical row with NUMTRADES > 0."
        ),
    }

    write_json(output_dir / "audit_summary.json", summary)

    partial_path = output_dir / "audit_by_security_partial.csv"
    if partial_path.exists():
        partial_path.unlink()

    print("\n========== MOEX UNIVERSE AUDIT ==========")
    print(f"Candidate common shares: {len(candidate_secids)}")
    print(f"Approved boards:         {', '.join(sorted(ALLOWED_BOARDS))}")
    print(f"Actually traded:         {counts['traded']}")
    print(f"No trade:                {counts['no_trade']}")
    print(f"No approved board:       {counts['no_approved_board']}")
    print(f"Audit errors:            {counts['audit_error']}")
    print("-----------------------------------------")
    print(f"Final universe:          {len(traded_universe)}")
    print(f"Output: {output_dir.resolve()}")
    print("=========================================\n")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        LOG.warning("Interrupted. Cached responses are preserved.")
        raise SystemExit(130)
    except Exception:
        LOG.exception("Universe audit failed")
        raise SystemExit(1)
