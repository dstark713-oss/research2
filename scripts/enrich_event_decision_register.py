
# Path: scripts/enrich_event_decision_register.py
# Description: Adds previously established forensic observations to the Stage 3.2 event decision register without changing existing decisions.

import csv
import json
import os
import tempfile
from collections import Counter
from pathlib import Path


INPUT_FILE = Path(
    "data/reports/forensic_stage3_2/event_decision_register.csv"
)

OUTPUT_FILE = Path(
    "data/reports/forensic_stage3_2/event_decision_register_enriched.csv"
)

SUMMARY_FILE = Path(
    "data/reports/forensic_stage3_2/enrichment_summary.json"
)


ANALYST_NOTES = {
    "VTBR_TQBR_2024-07-08_2024-07-15": (
        "Confirmed 5000:1 consolidation. Previous close multiplied by "
        "5000 equals 99.65; current close is 92.95. Residual movement "
        "after scale adjustment is approximately -6.72%. "
        "Corporate action adjustment is required."
    ),

    "IRAO_TQBR_2014-12-18_2015-01-20": (
        "Confirmed 100:1 consolidation. Adjusted previous close is 0.712 "
        "versus current close 0.7641. Residual change is approximately "
        "+7.32% over 33 calendar days. No additional scale anomaly confirmed."
    ),

    "GMKN_TQBR_2024-04-01_2024-04-08": (
        "Confirmed 100:1 consolidation effective 2024-04-04. "
        "The observed price discontinuity is explained by the corporate action."
    ),

    "ROLO_TQBR_2023-01-10_2023-01-19": (
        "Confirmed 1:10 split effective 2023-01-17. Adjusted previous "
        "close is approximately 0.7955; current close is 0.8999. "
        "Residual market movement is approximately +13.1%."
    ),

    "GEMA_TQBR_2024-02-01_2024-02-08": (
        "Confirmed 1:10 split effective 2024-02-08. Previous close "
        "divided by 10 equals 127.8; current close is 155.85. "
        "The remaining difference is market movement, not another "
        "scale mismatch."
    ),

    "BELU_TQBR_2024-08-15_2024-08-22": (
        "Distribution of 7 additional shares per existing share, "
        "8 shares total. The 2024-08-22 daily OHLC bar appears to contain "
        "mixed price scales: open/high near the old scale, low/close "
        "near the adjusted scale. Do not uniformly divide the entire bar "
        "by 8. Requires dedicated treatment and ideally trade-level evidence."
    ),

    "RGSS_TQBR_2016-04-25_2016-04-28": (
        "Possible 1:15 corporate action. Secondary sources and official "
        "exchange information indicate conflicting dates. The April event "
        "must not be linked to the later official date without reconciling "
        "the discrepancy. Remains unresolved."
    ),

    "PAZA_TQBR_2024-02-12_2024-12-09": (
        "Possible 1:2 scale change based on price ratio. No confirmed "
        "corporate-action source or effective date established. "
        "The 301-calendar-day interval is not a one-day return."
    ),

    "VTEL_EQBR_2005-12-20_2009-09-22": (
        "Observed price ratio is exactly 1:2 across 1372 calendar days. "
        "Possible corporate action, but no confirming source or effective "
        "date established. This is not a one-day return."
    ),

    "AFKS_TQBR_2014-12-17_2014-12-18": (
        "User-confirmed genuine market event. Price rose sharply in one "
        "session and encountered exchange price limits. Do not classify "
        "the move as a data error based on magnitude."
    ),

    "USBN_TQBR_2015-11-03_2015-11-05": (
        "User-confirmed genuine market price movement. The subsequent "
        "2015-11-05 to 2015-11-06 event is part of the same observed "
        "price episode."
    ),

    "USBN_TQBR_2015-11-05_2015-11-06": (
        "User-confirmed continuation of genuine market price movement. "
        "Do not infer a corporate action solely from the approximate "
        "price ratio."
    ),

    "GTRK_TQBR_2023-07-26_2023-07-27": (
        "User-confirmed genuine speculative market episode with "
        "pump-and-dump characteristics. Exchange discrete-auction "
        "context was identified in the subsequent forensic review."
    ),

    "GTRK_TQBR_2023-08-31_2023-09-01": (
        "User-confirmed continuation of the speculative market episode. "
        "Treat as genuine market behavior, not automatically as a data defect."
    ),

    "BLNG_TQBR_2023-02-07_2023-02-08": (
        "User-confirmed genuine speculative market episode with "
        "pump-and-dump characteristics."
    ),

    "LPSB_TQBR_2015-05-05_2015-05-08": (
        "User-confirmed genuine price movement in an illiquid share. "
        "Only 1 and 2 trades at the endpoints. Magnitude alone is not "
        "evidence of a data error; price may be non-representative."
    ),

    "LPSB_TQBR_2023-08-02_2024-12-09": (
        "User-confirmed genuine price movement in an illiquid share. "
        "495 calendar days between active observations. This is not "
        "a one-day return and should not be interpreted as one."
    ),

    "LPSB_TQBR_2014-11-05_2014-12-15": (
        "User-confirmed genuine price movement in an illiquid share. "
        "One trade at each endpoint and 27 zero-trade rows in between. "
        "Not suitable for automatic one-day return interpretation."
    ),

    "LPSB_TQBR_2017-05-02_2017-05-03": (
        "User-confirmed genuine price movement in an illiquid share. "
        "Low trade counts; do not classify as a data error solely "
        "because of the magnitude."
    ),

    "POGR_TQBR_2022-07-11_2022-07-12": (
        "Forensic review found a strong genuine market episode: "
        "event-day range 0.70-3.765, close 0.87, 74,132 trades, "
        "volume approximately 286.6 million and value approximately "
        "577 million. No evidence supports calling this a data error. "
        "Issuer/corporate-action context was not fully resolved; "
        "retain UNRESOLVED pending that check."
    ),

    "PHOR_TQBR_2013-08-14_2013-08-15": (
        "Previous endpoint: 1 trade, 2 shares. Current endpoint: "
        "6 trades, 30 shares. Wide intraday range and low trade counts "
        "make the closing prices potentially non-representative. "
        "Do not interpret the 119.9% change as a robust market return."
    ),

    "TAER_TQBR_2015-10-20_2015-10-21": (
        "Large move was accompanied by 39,592 trades, so this was not "
        "an isolated one-share print. Price subsequently reversed across "
        "following sessions. No split was confirmed. Treat as an unresolved "
        "real-market episode, not an automatic data error."
    ),

    "TAER_TQBR_2015-10-27_2015-10-28": (
        "Part of the broader volatile TAER episode in October 2015. "
        "The earlier sharp rise and subsequent reversal were observed. "
        "No corporate action confirmed for this event."
    ),

    "SELG_TQBR_2016-02-19_2016-02-20": (
        "Price approximately doubled; next active observation closed "
        "at 8.79, remaining above the pre-event level before later "
        "declining. No corporate action confirmed. Market-event "
        "interpretation remains provisional."
    ),

    "PRFN_TQBR_2019-10-08_2023-07-19": (
        "1380 calendar days between active observations. No intermediate "
        "rows in the extracted interval. Absence of rows does not prove "
        "absence of trading on every intervening date. The observed "
        "218% change is not a one-day return."
    ),

    "RDRB_TQBR_2016-04-20_2019-12-12": (
        "1331 calendar days between active observations and 922 "
        "zero-trade rows. Only 3 trades at the previous endpoint. "
        "The price difference is not a one-day return and may reflect "
        "a long period of sparse or absent activity."
    ),

    "MUGS_TQBR_2017-12-05_2017-12-06": (
        "Very sparse trading: 1 and 2 trades at the endpoints. "
        "Other MUGS events also show repeated one- or two-trade "
        "price changes and long gaps. Treat prices as potentially "
        "non-representative."
    ),

    "MUGS_TQBR_2017-11-23_2017-11-24": (
        "Only 5 and 3 trades at the endpoints. Part of a broader pattern "
        "of sparse MUGS observations and potentially non-representative "
        "closing prices."
    ),

    "MUGS_TQBR_2018-06-22_2018-07-20": (
        "28 calendar days between endpoints, one trade at each endpoint "
        "and 19 zero-trade rows. Not a one-day return."
    ),

    "MUGS_TQBR_2015-12-30_2016-10-10": (
        "285 calendar days between endpoints, 4 and 1 trades at endpoints "
        "and 193 zero-trade rows. Not a one-day return."
    ),

    "MUGS_TQBR_2016-10-10_2016-10-17": (
        "One trade at each endpoint. Part of a repeated pattern of "
        "sparse observations; price changes may not represent "
        "executable market prices."
    ),

    "IDVP_TQBR_2020-04-01_2020-04-08": (
        "One trade and one share at each endpoint. Nominal price "
        "movement is not a reliable indication of broad market value "
        "or executable liquidity."
    ),

    "IDVP_TQBR_2020-02-14_2020-02-25": (
        "One trade and one share at each endpoint. Treat the observed "
        "price change as a potentially non-representative mark."
    ),

    "IDVP_TQBR_2016-03-11_2016-03-21": (
        "One share traded at the previous endpoint; only 6 shares "
        "at the current endpoint. Price movement has very low "
        "representativeness."
    ),

    "IDVP_TQBR_2018-01-10_2018-04-20": (
        "100 calendar days between endpoints, one trade and one share "
        "at each endpoint, with 69 zero-trade rows. Not a one-day return."
    ),

    "IDVP_TQBR_2020-04-01_2020-04-08": (
        "One trade and one share at each endpoint. Nominal price "
        "movement is not a reliable indication of broad market value "
        "or executable liquidity."
    ),

    "IDVP_TQBR_2020-02-14_2020-02-25": (
        "One trade and one share at each endpoint. Treat the observed "
        "price change as a potentially non-representative mark."
    ),

    "IDVP_TQBR_2016-03-11_2016-03-21": (
        "One share traded at the previous endpoint; only 6 shares "
        "at the current endpoint. Price movement has very low "
        "representativeness."
    ),

    "IDVP_TQBR_2018-01-10_2018-04-20": (
        "100 calendar days between endpoints, one trade and one share "
        "at each endpoint, with 69 zero-trade rows. Not a one-day return."
    ),

    "DALM_TQBR_2014-07-17_2014-08-27": (
        "41 calendar days between endpoints, one trade and one share "
        "at the previous endpoint and 28 zero-trade rows. Not a one-day "
        "return; endpoint prices may be non-representative."
    ),

    "VRSB_TQBR_2015-04-09_2015-06-19": (
        "71 calendar days between endpoints, 46 zero-trade rows and "
        "only 2 trades at each endpoint. Not a one-day return."
    ),

    "VRSB_TQBR_2014-11-18_2014-11-25": (
        "One trade at each endpoint. Low trade counts make the observed "
        "price change potentially non-representative."
    ),

    "PAZA_TQBR_2024-02-12_2024-12-09": (
        "Possible 1:2 corporate action based on price ratio, but no "
        "confirmed source or effective date. 301 calendar days between "
        "active observations; do not interpret as a one-day return."
    ),

    "VTEL_EQBR_2005-12-20_2009-09-22": (
        "Possible 1:2 corporate action based on exact price ratio, "
        "but no confirmed source or effective date. 1372 calendar days "
        "between active observations; do not interpret as a one-day return."
    ),
}


def atomic_write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=path.stem + "_",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(fd)

    try:
        with open(temp_name, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

        os.replace(temp_name, path)

    except Exception:
        if os.path.exists(temp_name):
            os.remove(temp_name)
        raise


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    with open(INPUT_FILE, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "event_id" not in reader.fieldnames:
            raise ValueError("Input CSV must contain event_id column.")

        rows = list(reader)
        original_fields = list(reader.fieldnames)

    if len(rows) != 69:
        raise ValueError(
            f"Expected 69 events, found {len(rows)}. "
            "Check the input file before enrichment."
        )

    event_ids = [row["event_id"] for row in rows]

    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Duplicate event_id values found.")

    if "analyst_notes" not in original_fields:
        fieldnames = original_fields + ["analyst_notes"]
    else:
        fieldnames = original_fields

    notes_added = 0
    unmatched_note_ids = []

    for row in rows:
        event_id = row["event_id"]
        note = ANALYST_NOTES.get(event_id)

        if note:
            row["analyst_notes"] = note
            notes_added += 1
        elif "analyst_notes" not in row:
            row["analyst_notes"] = ""
        else:
            row["analyst_notes"] = row.get("analyst_notes", "")

    source_ids = set(event_ids)

    for event_id in ANALYST_NOTES:
        if event_id not in source_ids:
            unmatched_note_ids.append(event_id)

    atomic_write_csv(OUTPUT_FILE, rows, fieldnames)

    classification_counts = Counter(
        row.get("classification", "") for row in rows
    )

    summary = {
        "events": len(rows),
        "analyst_notes_added": notes_added,
        "events_without_additional_notes": len(rows) - notes_added,
        "unmatched_note_ids": unmatched_note_ids,
        "classifications_unchanged": dict(classification_counts),
        "input_file": str(INPUT_FILE.resolve()),
        "output_file": str(OUTPUT_FILE.resolve()),
        "source_register_modified": False,
        "price_adjustments_applied": False,
        "return_exclusions_applied": False,
        "universe_exclusions_applied": False,
    }

    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nStage 3.2 forensic notes enrichment completed.")


if __name__ == "__main__":
    main()
