
# Path: scripts/build_event_decision_register.py
# Description: Build and validate the Stage 3.2 event decision register.

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_INPUT = (
    PROJECT_ROOT
    / "data"
    / "reports"
    / "forensic_stage3_1"
    / "event_summary.csv"
)

DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "reports"
    / "forensic_stage3_2"
)

EXPECTED_EVENTS = 69

# ---------------------------------------------------------------------
# Confirmed real market events, based on the user's explicit review.
# ---------------------------------------------------------------------

REAL_EVENTS = {
    "LPSB_TQBR_2015-05-05_2015-05-08": (
        "REAL_ILLIQUID_MOVE",
        "User-confirmed genuine price movement in an illiquid share. "
        "Magnitude alone is not evidence of a data error.",
    ),
    "LPSB_TQBR_2023-08-02_2024-12-09": (
        "REAL_ILLIQUID_MOVE",
        "User-confirmed genuine price movement. Long interval between "
        "active observations; not a one-day return.",
    ),
    "LPSB_TQBR_2014-11-05_2014-12-15": (
        "REAL_ILLIQUID_MOVE",
        "User-confirmed genuine price movement in an illiquid share. "
        "Sparse trades and zero-trade observations make the price change "
        "unsuitable for automatic one-day interpretation.",
    ),
    "LPSB_TQBR_2017-05-02_2017-05-03": (
        "REAL_ILLIQUID_MOVE",
        "User-confirmed genuine price movement in an illiquid share.",
    ),
    "USBN_TQBR_2015-11-03_2015-11-05": (
        "REAL_MARKET_MOVE",
        "User-confirmed genuine market price movement.",
    ),
    "USBN_TQBR_2015-11-05_2015-11-06": (
        "REAL_MARKET_MOVE",
        "User-confirmed genuine market price movement.",
    ),
    "AFKS_TQBR_2014-12-17_2014-12-18": (
        "REAL_MARKET_MOVE",
        "User-confirmed real one-day market event. Price moved sharply "
        "and encountered exchange price limits.",
    ),
    "GTRK_TQBR_2023-07-26_2023-07-27": (
        "REAL_MARKET_MOVE",
        "User-confirmed genuine speculative market episode with "
        "pump-and-dump characteristics.",
    ),
    "GTRK_TQBR_2023-08-31_2023-09-01": (
        "REAL_MARKET_MOVE",
        "User-confirmed continuation of a genuine speculative market "
        "episode with pump-and-dump characteristics.",
    ),
    "BLNG_TQBR_2023-02-07_2023-02-08": (
        "REAL_MARKET_MOVE",
        "User-confirmed genuine speculative market episode with "
        "pump-and-dump characteristics.",
    ),
}


# ---------------------------------------------------------------------
# Confirmed corporate actions.
# Dates describe the effective market transition used for this register.
# Price adjustments are NOT performed by this script.
# ---------------------------------------------------------------------

CORPORATE_ACTIONS = {
    "VTBR_TQBR_2024-07-08_2024-07-15": {
        "ratio": "5000:1 consolidation",
        "date": "2024-07-15",
        "evidence": (
            "Confirmed 5000:1 consolidation. Previous close multiplied "
            "by 5000 gives 99.65; current close is 92.95. Residual market "
            "change after scale adjustment is approximately -6.72%."
        ),
    },
    "IRAO_TQBR_2014-12-18_2015-01-20": {
        "ratio": "100:1 consolidation",
        "date": "2015-01-20",
        "evidence": (
            "Confirmed 100:1 consolidation. Previous close multiplied "
            "by 100 gives 0.712; current close is 0.7641. Residual change "
            "is approximately +7.32% over 33 calendar days."
        ),
    },
    "GMKN_TQBR_2024-04-01_2024-04-08": {
        "ratio": "100:1 consolidation",
        "date": "2024-04-04",
        "evidence": (
            "Confirmed 100:1 corporate action effective 2024-04-04. "
            "The price scale changes from approximately 15054 to 152.96."
        ),
    },
    "ROLO_TQBR_2023-01-10_2023-01-19": {
        "ratio": "1:10 split",
        "date": "2023-01-17",
        "evidence": (
            "Confirmed 1:10 split effective 2023-01-17. Adjusted previous "
            "close is approximately 0.7955; current close is 0.8999. "
            "Residual change is approximately +13.1%."
        ),
    },
    "GEMA_TQBR_2024-02-01_2024-02-08": {
        "ratio": "1:10 split",
        "date": "2024-02-08",
        "evidence": (
            "Confirmed 1:10 split. Previous close divided by 10 is 127.8; "
            "current close is 155.85. The remaining difference is a "
            "market movement, not a further scale mismatch."
        ),
    },
    "BELU_TQBR_2024-08-15_2024-08-22": {
        "ratio": "1:8 distribution",
        "date": "2024-08-22",
        "evidence": (
            "Confirmed distribution of 7 additional shares per existing "
            "share, giving 8 shares in total. The 2024-08-22 daily OHLC "
            "bar contains mixed price scales: open/high remain near the "
            "old scale while low/close are near the adjusted scale. "
            "Requires dedicated handling; do not divide all OHLC values "
            "by 8 automatically."
        ),
    },
}


# ---------------------------------------------------------------------
# Cases requiring source/date/price-scale review.
# These are NOT confirmed corporate actions.
# ---------------------------------------------------------------------

SPECIAL_REVIEW_EVENTS = {
    "RGSS_TQBR_2016-04-25_2016-04-28": {
        "ratio": "POSSIBLE 1:15",
        "date": "NOT_ESTABLISHED",
        "review_status": "SOURCE_DATE_CONFLICT",
        "evidence": (
            "Possible 1:15 corporate action. Secondary sources and "
            "official exchange information indicate conflicting dates. "
            "Do not treat the action as confirmed until the date "
            "discrepancy is reconciled."
        ),
    },
    "PAZA_TQBR_2024-02-12_2024-12-09": {
        "ratio": "POSSIBLE 1:2",
        "date": "NOT_ESTABLISHED",
        "review_status": "NEEDS_SOURCE_CHECK",
        "evidence": (
            "Possible 1:2 corporate action based on price-scale "
            "comparison. No confirmed corporate-action source or "
            "effective date established."
        ),
    },
    "VTEL_EQBR_2005-12-20_2009-09-22": {
        "ratio": "POSSIBLE 1:2",
        "date": "NOT_ESTABLISHED",
        "review_status": "NEEDS_SOURCE_CHECK",
        "evidence": (
            "Observed price ratio is exactly 1:2 across a long calendar "
            "interval. Possible corporate action, but no confirming "
            "source or effective date established."
        ),
    },
}



REQUIRED_COLUMNS = {
    "event_id",
    "secid",
    "boardid",
    "previous_date",
    "current_date",
    "previous_close",
    "current_close",
}



def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        if not reader.fieldnames:
            raise ValueError("Input CSV has no header.")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"Input CSV is missing required columns: {sorted(missing)}"
            )

        rows = list(reader)

    if not rows:
        raise ValueError("Input CSV contains no events.")

    return rows


def read_existing_register(path: Path) -> Dict[str, Dict[str, str]]:
    """Load only final manually reviewed decisions from a prior register."""
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "event_id" not in reader.fieldnames:
            return {}

        result = {}
        for row in reader:
            if row.get("review_status") == "MANUAL_REVIEWED":
                result[row["event_id"]] = row

        return result


def apply_base_defaults(row: Dict[str, str]) -> None:
    row["classification"] = "UNRESOLVED"
    row["confidence"] = "UNASSESSED"
    row["evidence_summary"] = "NO_EVENT_SPECIFIC_EVIDENCE_RECORDED"
    row["corporate_action_ratio"] = "NOT_ESTABLISHED"
    row["corporate_action_date"] = "NOT_ESTABLISHED"
    row["price_adjustment_required"] = "UNASSESSED"
    row["exclude_from_returns"] = "UNASSESSED"
    row["event_treatment"] = "UNASSESSED"
    row["universe_action"] = "NO_DECISION"
    row["review_status"] = "UNREVIEWED"


def apply_real_event(row: Dict[str, str]) -> None:
    event_id = row["event_id"]
    classification, evidence = REAL_EVENTS[event_id]

    row["classification"] = classification
    row["confidence"] = "HIGH"
    row["evidence_summary"] = evidence
    row["corporate_action_ratio"] = "NOT_ESTABLISHED"
    row["corporate_action_date"] = "NOT_ESTABLISHED"
    row["price_adjustment_required"] = "NO"
    row["event_treatment"] = "MANUAL_REVIEW_REQUIRED"
    row["review_status"] = "PRELIMINARY_USER_CONFIRMED"


def apply_corporate_action(row: Dict[str, str]) -> None:
    event_id = row["event_id"]
    action = CORPORATE_ACTIONS[event_id]

    row["classification"] = "CORPORATE_ACTION_CONFIRMED"
    row["confidence"] = "HIGH"
    row["evidence_summary"] = action["evidence"]
    row["corporate_action_ratio"] = action["ratio"]
    row["corporate_action_date"] = action["date"]
    row["review_status"] = "PRELIMINARY_CONFIRMED"

    if event_id == "BELU_TQBR_2024-08-15_2024-08-22":
        row["price_adjustment_required"] = "SPECIAL_HANDLING"
        row["event_treatment"] = "MANUAL_REVIEW_REQUIRED"
        row["review_status"] = "NEEDS_PRICE_SCALE_REVIEW"
    else:
        row["price_adjustment_required"] = "YES"
        row["event_treatment"] = "CORPORATE_ACTION_ADJUSTMENT"


def apply_special_review(row: Dict[str, str]) -> None:
    event_id = row["event_id"]
    item = SPECIAL_REVIEW_EVENTS[event_id]

    row["classification"] = "UNRESOLVED"
    row["confidence"] = "LOW"
    row["evidence_summary"] = item["evidence"]
    row["corporate_action_ratio"] = item["ratio"]
    row["corporate_action_date"] = item["date"]
    row["price_adjustment_required"] = "UNASSESSED"
    row["event_treatment"] = "MANUAL_REVIEW_REQUIRED"
    row["review_status"] = item["review_status"]


def preserve_final_manual_decision(
    row: Dict[str, str],
    previous: Dict[str, str] | None,
) -> None:
    if not previous:
        return

    # A final decision is preserved only when explicitly marked as such.
    if previous.get("review_status") != "MANUAL_REVIEWED":
        return

    for key, value in previous.items():
        if key in row and value not in (None, ""):
            row[key] = value


def build_register(
    source_rows: List[Dict[str, str]],
    previous_decisions: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    result = []
    seen_ids = set()

    for source in source_rows:
        row = dict(source)
        event_id = row.get("event_id", "").strip()

        if not event_id:
            raise ValueError("Found row with empty event_id.")

        if event_id in seen_ids:
            raise ValueError(f"Duplicate event_id: {event_id}")

        seen_ids.add(event_id)
        row["event_id"] = event_id

        apply_base_defaults(row)

        if event_id in CORPORATE_ACTIONS:
            apply_corporate_action(row)
        elif event_id in REAL_EVENTS:
            apply_real_event(row)
        elif event_id in SPECIAL_REVIEW_EVENTS:
            apply_special_review(row)

        preserve_final_manual_decision(
            row,
            previous_decisions.get(event_id),
        )

        # These fields are deliberately never inferred by this script.
        # A reviewer must make these decisions explicitly.
        if row.get("review_status") != "MANUAL_REVIEWED":
            row["exclude_from_returns"] = "UNASSESSED"
            row["universe_action"] = "NO_DECISION"

        result.append(row)

    return result


def validate_register(rows: List[Dict[str, str]]) -> Dict[str, object]:
    if len(rows) != EXPECTED_EVENTS:
        raise ValueError(
            f"Expected {EXPECTED_EVENTS} events, found {len(rows)}."
        )

    ids = [row["event_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate event IDs in output.")

    classifications = Counter(row["classification"] for row in rows)
    treatments = Counter(row["event_treatment"] for row in rows)
    review_statuses = Counter(row["review_status"] for row in rows)

    expected_classifications = {
        "CORPORATE_ACTION_CONFIRMED": 6,
        "REAL_ILLIQUID_MOVE": 4,
        "REAL_MARKET_MOVE": 6,
        "UNRESOLVED": 53,
    }

    expected_treatments = {
        "CORPORATE_ACTION_ADJUSTMENT": 5,
        "MANUAL_REVIEW_REQUIRED": 14,
        "UNASSESSED": 50,
    }

    if dict(classifications) != expected_classifications:
        raise ValueError(
            "Classification counts do not match expected values: "
            f"{dict(classifications)}"
        )

    if dict(treatments) != expected_treatments:
        raise ValueError(
            "Treatment counts do not match expected values: "
            f"{dict(treatments)}"
        )

    # No automatic exclusions or universe removals are allowed here.
    for row in rows:
        if row["review_status"] != "MANUAL_REVIEWED":
            if row["exclude_from_returns"] != "UNASSESSED":
                raise ValueError(
                    f"Unexpected return exclusion decision: {row['event_id']}"
                )
            if row["universe_action"] != "NO_DECISION":
                raise ValueError(
                    f"Unexpected universe decision: {row['event_id']}"
                )

    return {
        "events": len(rows),
        "classifications": dict(classifications),
        "event_treatments": dict(treatments),
        "review_statuses": dict(review_statuses),
        "exclude_from_returns": dict(
            Counter(row["exclude_from_returns"] for row in rows)
        ),
        "universe_actions": dict(
            Counter(row["universe_action"] for row in rows)
        ),
    }


def atomic_write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(rows[0].keys())
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}_",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fieldnames,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(rows)

        os.replace(temporary_name, path)

    except Exception:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)
        raise


def atomic_write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}_",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")

        os.replace(temporary_name, path)

    except Exception:
        if os.path.exists(temporary_name):
            os.remove(temporary_name)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the Stage 3.2 event decision register."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Stage 3.1 event_summary.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for Stage 3.2",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()

    output_csv = output_dir / "event_decision_register.csv"
    output_json = output_dir / "summary.json"

    source_rows = read_csv(input_path)

    # Preserve only explicitly final manual decisions from an earlier run.
    previous_decisions = read_existing_register(output_csv)

    register = build_register(source_rows, previous_decisions)
    summary = validate_register(register)

    summary.update({
        "input_file": str(input_path),
        "output_file": str(output_csv),
        "final_manual_decisions_preserved": len(previous_decisions),
        "raw_database_modified": False,
        "price_adjustments_applied": False,
        "automatic_return_exclusions": 0,
        "automatic_universe_exclusions": 0,
    })

    atomic_write_csv(output_csv, register)
    atomic_write_json(output_json, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nStage 3.2 event decision register created successfully.")


if __name__ == "__main__":
    main()
