
# Folder: scripts/alt_view/
# File: extreme_returns_audit.py

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kurtosis

ROOT = Path(__file__).resolve().parents[2]

INPUT = ROOT / "data/reports/risk_control/daily_returns.csv"
OUTPUT = ROOT / "data/reports/alt_view/normality"

OUTPUT.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(INPUT)

# Ищем колонку с датой, если она есть.
date_candidates = [
    c for c in df.columns
    if c.lower() in {"date", "tradedate", "trade_date"}
]

date_col = date_candidates[0] if date_candidates else None

results = []

for col in df.select_dtypes(include=np.number).columns:
    x = pd.to_numeric(df[col], errors="coerce")
    valid = x.replace([np.inf, -np.inf], np.nan).dropna()

    if len(valid) < 10:
        continue

    # Индексы исходных строк для восстановления дат.
    worst = valid.nsmallest(5)
    best = valid.nlargest(5)

    print(f"\n{'=' * 65}")
    print(f"SERIES: {col}")
    print(f"Observations: {len(valid)}")
    print(f"Original excess kurtosis: {kurtosis(valid, fisher=True):.3f}")

    for label, observations in [
        ("WORST", worst),
        ("BEST", best),
    ]:
        print(f"\n{label} observations:")

        for idx, value in observations.items():
            date = df.loc[idx, date_col] if date_col else idx
            print(f"  {date}: {value:.6f}")

    # Анализ чувствительности к экстремальным наблюдениям.
    sorted_abs = valid.abs().sort_values(ascending=False)

    for k in [1, 2, 5]:
        excluded_indices = sorted_abs.head(k).index
        reduced = valid.drop(index=excluded_indices)

        results.append({
            "series": col,
            "excluded_extremes": k,
            "n_remaining": len(reduced),
            "excess_kurtosis": kurtosis(
                reduced, fisher=True
            ),
            "skewness": reduced.skew(),
        })

audit = pd.DataFrame(results)

audit.to_csv(
    OUTPUT / "extreme_returns_sensitivity.csv",
    index=False
)

print("\nSaved:", OUTPUT / "extreme_returns_sensitivity.csv")
