# Path: scripts/risk/data.py
# Description: Load adjusted MOEX prices from SQLite without modifying source data.

import sqlite3
from pathlib import Path

import pandas as pd

from config import DB_PATH, TICKERS, APPROVED_BOARDS


def connect_readonly(db_path: Path):
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"

    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")

    return connection


def find_price_table(connection):
    tables = connection.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
    """).fetchall()

    required = {
        "secid",
        "tradedate",
        "adjusted_close",
        "boardid",
        "numtrades",
    }

    candidates = []

    for (table_name,) in tables:
        columns = connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()

        available = {
            row[1].lower()
            for row in columns
        }

        if required.issubset(available):
            candidates.append(table_name)

    if not candidates:
        raise RuntimeError(
            "No table found with SECID, TRADEDATE, "
            "ADJUSTED_CLOSE, BOARDID and NUMTRADES."
        )

    if len(candidates) > 1:
        preferred = [
            name for name in candidates
            if "adjusted" in name.lower()
        ]

        if len(preferred) == 1:
            return preferred[0]

        raise RuntimeError(
            f"Multiple candidate tables found: {candidates}. "
            "Specify the correct table explicitly."
        )

    return candidates[0]


def load_prices(db_path=DB_PATH):
    connection = connect_readonly(db_path)

    try:
        table = find_price_table(connection)

        placeholders = ",".join("?" for _ in TICKERS)
        board_placeholders = ",".join(
            "?" for _ in APPROVED_BOARDS
        )

        sql = f"""
            SELECT
                secid,
                boardid,
                tradedate,
                numtrades,
                adjusted_close
            FROM "{table}"
            WHERE secid IN ({placeholders})
              AND boardid IN ({board_placeholders})
              AND numtrades > 0
              AND adjusted_close IS NOT NULL
              AND adjusted_close > 0
            ORDER BY tradedate, secid
        """

        params = list(TICKERS) + list(APPROVED_BOARDS)

        rows = pd.read_sql_query(
            sql,
            connection,
            params=params,
        )

    finally:
        connection.close()

    if rows.empty:
        raise RuntimeError("No active adjusted price records found.")

    rows["tradedate"] = pd.to_datetime(rows["tradedate"])

    # Prefer TQBR when both approved boards have active records.
    board_priority = {
        "TQBR": 0,
        "EQBR": 1,
    }

    rows["board_priority"] = rows["boardid"].map(board_priority)

    rows = rows.sort_values(
        ["tradedate", "secid", "board_priority"]
    )

    rows = rows.drop_duplicates(
        subset=["tradedate", "secid"],
        keep="first",
    )

    prices = rows.pivot(
        index="tradedate",
        columns="secid",
        values="adjusted_close",
    )

    prices = prices.reindex(columns=TICKERS)
    prices = prices.sort_index()

    missing = [
        ticker for ticker in TICKERS
        if ticker not in prices.columns
        or prices[ticker].notna().sum() == 0
    ]

    if missing:
        raise RuntimeError(
            f"No usable history for tickers: {missing}"
        )

    first_dates = prices.apply(
        lambda column: column.first_valid_index()
    )

    last_dates = prices.apply(
        lambda column: column.last_valid_index()
    )

    # Use a common observation period for all eight securities.
    common_start = max(first_dates)
    common_end = min(last_dates)

    prices = prices.loc[
        (prices.index >= common_start)
        & (prices.index <= common_end)
    ]

    # Forward fill is used only for portfolio mark-to-market.
    # It does not modify the source database.
    prices = prices.ffill()

    prices = prices.dropna(how="any")

    if prices.empty:
        raise RuntimeError("Common price history is empty.")

    print(f"Source table: {table}")
    print(f"Tickers: {len(TICKERS)}")
    print(f"Common period: {prices.index.min().date()} "
          f"- {prices.index.max().date()}")
    print(f"Observations: {len(prices):,}")

    return prices