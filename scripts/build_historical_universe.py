#!/usr/bin/env python3
"""Build a point-in-time MOEX common-share universe from ISS history.

The script deliberately uses historical ISS endpoints, never the current
security directory as the source of the historical universe.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://iss.moex.com/iss"
ENGINE = "stock"
MARKET = "shares"
PAGE_SIZE = 100
USER_AGENT = "research2-moex-iss-universe/0.1"


class ISSAccessError(RuntimeError):
    pass


class ISSClient:
    def __init__(self, delay: float = 0.15, retries: int = 5) -> None:
        self.delay = delay
        self.retries = retries

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"iss.meta": "off", **(params or {})}
        url = f"{BASE_URL}{path}?{urlencode(query)}"
        last_error: Exception | None = None
        for attempt in range(self.retries):
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urlopen(request, timeout=40) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                time.sleep(self.delay)
                return payload
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise ISSAccessError(
                        f"ISS вернул HTTP {exc.code} для {path}. "
                        "Проверьте права/подписку на исторические данные MOEX."
                    ) from exc
                if exc.code not in (429, 500, 502, 503, 504):
                    raise
                last_error = exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(min(2 ** attempt, 20))
        raise RuntimeError(f"ISS request failed after {self.retries} attempts: {url}") from last_error

    def table(self, path: str, block: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = self.get_json(path, {**(params or {}), "iss.only": block, "start": start, "limit": PAGE_SIZE})
            section = payload.get(block)
            if not isinstance(section, dict) or "columns" not in section or "data" not in section:
                raise RuntimeError(
                    f"ISS response has no expected '{block}' table for {path}. "
                    f"Top-level keys: {list(payload)}"
                )
            columns = section["columns"]
            batch = [dict(zip(columns, values)) for values in section["data"]]
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            start += len(batch)
        return rows


def normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("ё", "е").split())


def classify_security(metadata: dict[str, Any]) -> str:
    """Conservative classification: unknown instruments are not admitted."""
    type_value = normalize(metadata.get("TYPE"))
    group_value = normalize(metadata.get("GROUP"))
    sectype_value = normalize(metadata.get("SECTYPE"))
    combined = " | ".join((type_value, group_value, sectype_value))

    if any(token in combined for token in ("привилег", "preferred_share", "preferred share")):
        return "preferred_share"
    if "облигац" in combined or "bond" in combined:
        return "bond"
    if any(token in combined for token in ("обыкнов", "common_share", "common share")):
        return "common_share"
    return "unknown_or_other"


def get_security_metadata(client: ISSClient, secid: str) -> dict[str, Any]:
    payload = client.get_json(f"/securities/{secid}.json", {"iss.only": "description"})
    block = payload.get("description", {})
    columns = block.get("columns", [])
    result: dict[str, Any] = {}
    for row in block.get("data", []):
        item = dict(zip(columns, row))
        key = str(item.get("name", "")).upper()
        if key:
            result[key] = item.get("value")
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", default=None, help="Optional lower date bound YYYY-MM-DD; default: all ISS history")
    parser.add_argument("--to-date", default=date.today().isoformat(), help="Upper date bound YYYY-MM-DD")
    parser.add_argument("--output-dir", default="data/raw/moex_iss/universe")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between ISS requests")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = ISSClient(delay=args.delay)

    try:
        logging.info("Requesting historical trading-date calendar from ISS")
        dates = client.table(
            f"/history/engines/{ENGINE}/markets/{MARKET}/dates",
            "dates",
        )
        date_values = sorted({
            str(row.get("date") or row.get("TRADEDATE"))
            for row in dates
            if row.get("date") or row.get("TRADEDATE")
        })
        date_values = [
            value for value in date_values
            if (args.from_date is None or value >= args.from_date) and value <= args.to_date
        ]
        if not date_values:
            raise RuntimeError("ISS returned no historical trading dates in the requested interval")
        logging.info("Historical dates to scan: %s (%s — %s)", len(date_values), date_values[0], date_values[-1])

        raw_rows: list[dict[str, Any]] = []
        daily_pairs: set[tuple[str, str]] = set()
        for index, trade_date in enumerate(date_values, start=1):
            rows = client.table(
                f"/history/engines/{ENGINE}/markets/{MARKET}/securities",
                "history",
                {"date": trade_date, "tradingsession": 3},
            )
            for row in rows:
                secid = str(row.get("SECID") or "").strip()
                if not secid:
                    continue
                numtrades = row.get("NUMTRADES")
                try:
                    has_trades = numtrades is not None and float(numtrades) > 0
                except (TypeError, ValueError):
                    has_trades = False
                if not has_trades:
                    continue
                raw_rows.append({"TRADEDATE": trade_date, **row})
                daily_pairs.add((trade_date, secid))
            if index % 100 == 0 or index == len(date_values):
                logging.info("Scanned %s/%s dates; observed %s ticker-date pairs", index, len(date_values), len(daily_pairs))

        if not daily_pairs:
            raise RuntimeError("No ticker-date pairs with NUMTRADES > 0 were returned by ISS")

        secids = sorted({secid for _, secid in daily_pairs})
        logging.info("Unique securities with actual trades: %s", len(secids))
        security_master: list[dict[str, Any]] = []
        for index, secid in enumerate(secids, start=1):
            metadata = get_security_metadata(client, secid)
            classification = classify_security(metadata)
            security_master.append({
                "SECID": secid,
                "TYPE": metadata.get("TYPE"),
                "GROUP": metadata.get("GROUP"),
                "SECTYPE": metadata.get("SECTYPE"),
                "classification": classification,
                "eligible_common_share": classification == "common_share",
            })
            if index % 100 == 0 or index == len(secids):
                logging.info("Classified %s/%s securities", index, len(secids))

        classifications = {row["SECID"]: row["classification"] for row in security_master}
        eligible_pairs = sorted(
            (trade_date, secid)
            for trade_date, secid in daily_pairs
            if classifications.get(secid) == "common_share"
        )
        excluded = [
            row for row in security_master
            if row["classification"] != "common_share"
        ]

        # Raw all-instrument history is retained for audit; the filtered universe
        # is a separate derived artifact. Rows remain board-level in the raw file.
        raw_path = output_dir / "historical_market_rows_raw.jsonl"
        with raw_path.open("w", encoding="utf-8") as handle:
            for row in raw_rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

        daily_rows = [{"TRADEDATE": d, "SECID": s} for d, s in eligible_pairs]
        write_csv(output_dir / "universe_daily_common_shares.csv", daily_rows, ["TRADEDATE", "SECID"])
        write_csv(
            output_dir / "security_master.csv",
            security_master,
            ["SECID", "TYPE", "GROUP", "SECTYPE", "classification", "eligible_common_share"],
        )
        write_csv(
            output_dir / "universe_exclusions.csv",
            excluded,
            ["SECID", "TYPE", "GROUP", "SECTYPE", "classification", "eligible_common_share"],
        )

        interval_map: dict[str, list[str]] = defaultdict(list)
        for trade_date, secid in eligible_pairs:
            interval_map[secid].append(trade_date)
        intervals = []
        for secid, observed_dates in sorted(interval_map.items()):
            intervals.append({
                "SECID": secid,
                "first_observed_trade_date": min(observed_dates),
                "last_observed_trade_date": max(observed_dates),
                "observed_trade_days": len(set(observed_dates)),
            })
        write_csv(
            output_dir / "universe_intervals.csv",
            intervals,
            ["SECID", "first_observed_trade_date", "last_observed_trade_date", "observed_trade_days"],
        )

        summary = {
            "source": "MOEX ISS historical market results",
            "engine": ENGINE,
            "market": MARKET,
            "from_date": date_values[0],
            "to_date": date_values[-1],
            "historical_dates_scanned": len(date_values),
            "unique_securities_with_trades": len(secids),
            "ticker_date_pairs_all_instruments": len(daily_pairs),
            "unique_common_shares": sum(row["classification"] == "common_share" for row in security_master),
            "common_share_ticker_date_pairs": len(eligible_pairs),
            "excluded_by_classification": {
                label: sum(row["classification"] == label for row in excluded)
                for label in sorted({row["classification"] for row in excluded})
            },
            "classification_policy": "Only explicit common-share metadata is admitted; unknown/other is quarantined.",
            "note": "Observed trade dates are based on NUMTRADES > 0, not merely listing intervals.",
        }
        (output_dir / "universe_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logging.info("Universe build completed: %s", json.dumps(summary, ensure_ascii=False))
        return 0
    except ISSAccessError as exc:
        logging.error("%s", exc)
        return 2
    except (HTTPError, URLError) as exc:
        logging.exception("ISS network error: %s", exc)
        return 3
    except Exception as exc:
        logging.exception("Universe build failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
