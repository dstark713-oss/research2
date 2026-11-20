
# research2/scripts/eda/01_univariate_distributions.py

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

PANEL_PATH = ROOT / "data" / "derived" / "eda" / "eda_panel.parquet"

OUTPUT_DIR = ROOT / "reports" / "eda" / "01_univariate_distributions"
TABLE_DIR = OUTPUT_DIR / "tables"
FIGURE_DIR = OUTPUT_DIR / "figures"

REPORT_PATH = ROOT / "reports" / "eda" / "01_UNIVARIATE_DISTRIBUTIONS.md"

MIN_TICKER_OBS = 60
MAX_HIST_SAMPLE = 500_000
RANDOM_SEED = 42

QUANTILES = [
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.10,
    0.25,
    0.50,
    0.75,
    0.90,
    0.95,
    0.975,
    0.99,
    0.995,
    0.999,
]

# Features selected for distribution analysis.
FEATURES = [
    "return_1d",
    "abs_return",
    "squared_return",
    "high_low_range",
    "open_close_body",
    "overnight_gap",
    "volume",
    "value",
    "log_volume",
    "log_value",
    "rv_5",
    "rv_20",
    "mean_abs_return_5",
    "mean_abs_return_20",
    "value_sum_20",
    "value_sum_60",
    "value_mean_20",
    "value_mean_60",
    "volume_sum_20",
    "volume_sum_60",
    "value_to_median_20",
    "zero_value_share_20",
    "zero_volume_share_20",
]

# Variables for which zero is a meaningful observation.
ZERO_MEANINGFUL = {
    "return_1d",
    "abs_return",
    "squared_return",
    "volume",
    "value",
    "log_volume",
    "log_value",
    "zero_value_share_20",
    "zero_volume_share_20",
}

# Log-scale plots use log1p for non-negative variables.
LOG_PLOT_FEATURES = {
    "abs_return",
    "squared_return",
    "volume",
    "value",
    "log_volume",
    "log_value",
    "rv_5",
    "rv_20",
    "mean_abs_return_5",
    "mean_abs_return_20",
    "value_sum_20",
    "value_sum_60",
    "value_mean_20",
    "value_mean_60",
    "volume_sum_20",
    "volume_sum_60",
}


# ============================================================
# HELPERS
# ============================================================

def log(message: str) -> None:
    print(f"[EDA-01] {message}")


def ensure_dirs() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)


def load_panel() -> pd.DataFrame:
    if not PANEL_PATH.exists():
        raise FileNotFoundError(
            f"EDA panel not found: {PANEL_PATH}"
        )

    df = pd.read_parquet(PANEL_PATH)

    required = {"tradedate", "secid"}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Panel missing required columns: {sorted(missing)}"
        )

    df["tradedate"] = pd.to_datetime(df["tradedate"])
    df["secid"] = df["secid"].astype(str)

    if df.duplicated(["tradedate", "secid"]).any():
        raise ValueError("Duplicate date/ticker observations.")

    log(f"Loaded rows: {len(df):,}")
    log(f"Tickers: {df['secid'].nunique():,}")
    log(f"Dates: {df['tradedate'].nunique():,}")

    return df


def available_features(df: pd.DataFrame) -> list[str]:
    result = []

    for feature in FEATURES:
        if feature not in df.columns:
            log(f"SKIP missing feature: {feature}")
            continue

        if not pd.api.types.is_numeric_dtype(df[feature]):
            log(f"SKIP non-numeric feature: {feature}")
            continue

        result.append(feature)

    if not result:
        raise RuntimeError("No numeric features available.")

    return result


def clean_series(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan)
    return values.dropna()


def sample_for_plot(
    series: pd.Series,
    max_size: int = MAX_HIST_SAMPLE,
) -> pd.Series:
    if len(series) <= max_size:
        return series

    return series.sample(
        n=max_size,
        random_state=RANDOM_SEED,
    )


def safe_float(value) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


# ============================================================
# POOLED DISTRIBUTION STATISTICS
# ============================================================

def calculate_distribution_stats(
    series: pd.Series,
    feature: str,
) -> dict:
    original = series
    clean = clean_series(series)

    n_total = len(original)
    n_valid = len(clean)
    n_missing = n_total - n_valid

    result = {
        "feature": feature,
        "n_total": n_total,
        "n_valid": n_valid,
        "n_missing": n_missing,
        "missing_share": n_missing / n_total if n_total else np.nan,
        "n_zero": int((clean == 0).sum()),
        "zero_share": float((clean == 0).mean()) if n_valid else np.nan,
        "n_negative": int((clean < 0).sum()),
        "negative_share": float((clean < 0).mean()) if n_valid else np.nan,
        "n_positive": int((clean > 0).sum()),
        "positive_share": float((clean > 0).mean()) if n_valid else np.nan,
    }

    if n_valid == 0:
        return result

    result.update(
        {
            "mean": safe_float(clean.mean()),
            "median": safe_float(clean.median()),
            "variance": safe_float(clean.var(ddof=1)),
            "std": safe_float(clean.std(ddof=1)),
            "min": safe_float(clean.min()),
            "max": safe_float(clean.max()),
            "range": safe_float(clean.max() - clean.min()),
            "skewness": safe_float(clean.skew()),
            "excess_kurtosis": safe_float(clean.kurt()),
        }
    )

    q = clean.quantile(QUANTILES)

    for quantile, value in q.items():
        result[f"q_{quantile:g}"] = safe_float(value)

    q25 = clean.quantile(0.25)
    q50 = clean.quantile(0.50)
    q75 = clean.quantile(0.75)

    mad = (clean - q50).abs().median()

    result["iqr"] = safe_float(q75 - q25)
    result["mad"] = safe_float(mad)

    # Normal-consistent robust sigma estimate.
    result["robust_sigma"] = safe_float(1.4826 * mad)

    if result["robust_sigma"] and result["robust_sigma"] > 0:
        result["std_to_robust_sigma"] = (
            result["std"] / result["robust_sigma"]
        )
    else:
        result["std_to_robust_sigma"] = np.nan

    # Tail concentration.
    abs_values = clean.abs()

    for threshold in (0.01, 0.05, 0.10, 0.25, 0.50, 1.00):
        result[f"share_abs_gt_{threshold:g}"] = float(
            (abs_values > threshold).mean()
        )

    return result


def build_pooled_statistics(
    df: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    log("Calculating pooled distribution statistics...")

    records = []

    for feature in features:
        log(f"  {feature}")

        records.append(
            calculate_distribution_stats(
                df[feature],
                feature,
            )
        )

    return pd.DataFrame(records)


# ============================================================
# TICKER-LEVEL STATISTICS
# ============================================================

def build_ticker_statistics(
    df: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    log("Calculating ticker-level statistics...")

    records = []

    for ticker, group in df.groupby("secid", sort=False):
        for feature in features:
            values = clean_series(group[feature])

            if len(values) < MIN_TICKER_OBS:
                continue

            median = values.median()
            mad = (values - median).abs().median()

            records.append(
                {
                    "secid": ticker,
                    "feature": feature,
                    "n_obs": len(values),
                    "mean": safe_float(values.mean()),
                    "median": safe_float(median),
                    "std": safe_float(values.std(ddof=1)),
                    "variance": safe_float(values.var(ddof=1)),
                    "skewness": safe_float(values.skew()),
                    "excess_kurtosis": safe_float(values.kurt()),
                    "q05": safe_float(values.quantile(0.05)),
                    "q25": safe_float(values.quantile(0.25)),
                    "q75": safe_float(values.quantile(0.75)),
                    "q95": safe_float(values.quantile(0.95)),
                    "min": safe_float(values.min()),
                    "max": safe_float(values.max()),
                    "mad": safe_float(mad),
                    "robust_sigma": safe_float(1.4826 * mad),
                    "zero_share": float((values == 0).mean()),
                }
            )

    result = pd.DataFrame(records)

    if result.empty:
        raise RuntimeError(
            "No ticker-level statistics passed MIN_TICKER_OBS."
        )

    return result


def summarize_ticker_statistics(
    ticker_stats: pd.DataFrame,
) -> pd.DataFrame:
    records = []

    metrics = [
        "n_obs",
        "mean",
        "median",
        "std",
        "variance",
        "skewness",
        "excess_kurtosis",
        "q05",
        "q25",
        "q75",
        "q95",
        "mad",
        "robust_sigma",
        "zero_share",
    ]

    for feature, group in ticker_stats.groupby("feature"):
        for metric in metrics:
            values = clean_series(group[metric])

            if values.empty:
                continue

            records.append(
                {
                    "feature": feature,
                    "ticker_metric": metric,
                    "n_tickers": len(values),
                    "mean_across_tickers": values.mean(),
                    "median_across_tickers": values.median(),
                    "std_across_tickers": values.std(ddof=1),
                    "min_across_tickers": values.min(),
                    "q25_across_tickers": values.quantile(0.25),
                    "q75_across_tickers": values.quantile(0.75),
                    "max_across_tickers": values.max(),
                }
            )

    return pd.DataFrame(records)


# ============================================================
# EXTREME OBSERVATIONS
# ============================================================

def build_extreme_observations(
    df: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    log("Extracting extreme observations...")

    records = []

    for feature in features:
        values = clean_series(df[feature])

        if len(values) < 100:
            continue

        threshold = values.abs().quantile(0.999)

        mask = (
            df[feature].abs() >= threshold
        ) & df[feature].notna()

        selected = df.loc[
            mask,
            ["tradedate", "secid", feature],
        ].copy()

        selected["feature"] = feature
        selected["abs_value"] = selected[feature].abs()
        selected["q999_abs_threshold"] = threshold

        selected = selected.sort_values(
            "abs_value",
            ascending=False,
        ).head(100)

        records.append(selected)

    if not records:
        return pd.DataFrame()

    result = pd.concat(records, ignore_index=True)

    return result[
        [
            "feature",
            "tradedate",
            "secid",
            "return_1d" if "return_1d" in result.columns else "feature",
            "abs_value",
            "q999_abs_threshold",
        ]
    ] if False else result


# ============================================================
# PLOTS
# ============================================================

def plot_histograms(
    df: pd.DataFrame,
    features: list[str],
) -> list[str]:
    log("Creating distribution plots...")

    generated = []

    for feature in features:
        values = clean_series(df[feature])

        if len(values) < 10:
            continue

        values = sample_for_plot(values)

        fig, ax = plt.subplots(figsize=(10, 6))

        ax.hist(
            values,
            bins=100,
            density=True,
            alpha=0.75,
        )

        ax.set_title(f"{feature}: empirical distribution")
        ax.set_xlabel(feature)
        ax.set_ylabel("Density")
        ax.grid(alpha=0.2)

        path = FIGURE_DIR / f"{feature}_hist.png"

        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

        generated.append(str(path.relative_to(ROOT)))

        if feature in LOG_PLOT_FEATURES:
            positive = values[values >= 0]

            if len(positive) > 10:
                transformed = np.log1p(positive)

                fig, ax = plt.subplots(figsize=(10, 6))

                ax.hist(
                    transformed,
                    bins=100,
                    density=True,
                    alpha=0.75,
                )

                ax.set_title(f"{feature}: log1p distribution")
                ax.set_xlabel(f"log1p({feature})")
                ax.set_ylabel("Density")
                ax.grid(alpha=0.2)

                path = FIGURE_DIR / f"{feature}_log1p_hist.png"

                fig.tight_layout()
                fig.savefig(path, dpi=140)
                plt.close(fig)

                generated.append(str(path.relative_to(ROOT)))

    return generated


def plot_ticker_boxplots(
    ticker_stats: pd.DataFrame,
) -> list[str]:
    generated = []

    selected_features = [
        "return_1d",
        "abs_return",
        "rv_5",
        "rv_20",
        "volume",
        "value",
        "log_volume",
        "log_value",
    ]

    for feature in selected_features:
        group = ticker_stats[
            ticker_stats["feature"] == feature
        ]

        if group.empty:
            continue

        metric = "std" if feature in {
            "return_1d",
            "abs_return",
            "rv_5",
            "rv_20",
        } else "median"

        values = clean_series(group[metric])

        if len(values) < 3:
            continue

        fig, ax = plt.subplots(figsize=(9, 5))

        ax.boxplot(
            values,
            vert=True,
            showfliers=True,
        )

        ax.set_title(
            f"Ticker-level {metric}: {feature}"
        )
        ax.set_ylabel(metric)
        ax.grid(alpha=0.2)

        path = FIGURE_DIR / f"ticker_{feature}_{metric}_boxplot.png"

        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

        generated.append(str(path.relative_to(ROOT)))

    return generated


# ============================================================
# MARKDOWN REPORT
# ============================================================

def write_report(
    df: pd.DataFrame,
    pooled: pd.DataFrame,
    ticker_stats: pd.DataFrame,
    ticker_summary: pd.DataFrame,
    extremes: pd.DataFrame,
    figures: list[str],
) -> None:
    lines = [
        "# 01. Univariate Distributions",
        "",
        "## Research question",
        "",
        "What are the empirical distributional properties of each variable,",
        "and how strongly do these properties differ across tickers?",
        "",
        "## Observation unit",
        "",
        "One row represents one ticker on one market session.",
        "Observations are not assumed to be independent.",
        "",
        "## Dataset",
        "",
        f"- Rows: {len(df):,}",
        f"- Tickers: {df['secid'].nunique():,}",
        f"- Dates: {df['tradedate'].nunique():,}",
        f"- First date: {df['tradedate'].min().date()}",
        f"- Last date: {df['tradedate'].max().date()}",
        "",
        "## Methodology",
        "",
        f"- Minimum observations per ticker: {MIN_TICKER_OBS}",
        "- Pooled statistics are calculated over all available panel rows.",
        "- Ticker-level statistics are calculated independently per ticker.",
        "- Missing and infinite values are excluded per variable.",
        "- No winsorization or outlier deletion is applied.",
        "- Excess kurtosis uses Fisher definition (normal = 0).",
        "- Robust sigma = 1.4826 × median absolute deviation.",
        "- Histogram samples are capped for rendering only.",
        "",
        "## Pooled distribution statistics",
        "",
        "Full table: `tables/pooled_statistics.csv`",
        "",
        "## Across-ticker statistics",
        "",
        "Full table: `tables/ticker_statistics_summary.csv`",
        "",
        "Ticker-level records: `tables/ticker_statistics.csv`",
        "",
        "## Extreme observations",
        "",
        "Extreme observations use the 99.9th percentile of absolute values",
        "for each feature. They are flagged for inspection, not classified",
        "as errors.",
        "",
        "Full table: `tables/extreme_observations.csv`",
        "",
        "## Distribution plots",
        "",
    ]

    for figure in figures:
        relative_to_report = Path(figure).relative_to(
            Path("reports/eda")
        )

        lines.extend(
            [
                f"### {Path(figure).stem}",
                "",
                f"![{Path(figure).stem}]({relative_to_report.as_posix()})",
                "",
            ]
        )

    lines.extend(
        [
            "## Output files",
            "",
            "- `tables/pooled_statistics.csv`",
            "- `tables/ticker_statistics.csv`",
            "- `tables/ticker_statistics_summary.csv`",
            "- `tables/extreme_observations.csv`",
            "- `figures/*.png`",
            "",
            "## Limitations",
            "",
            "- Pooled distributions mix different tickers and time periods.",
            "- Cross-sectional differences may reflect different history lengths.",
            "- Extreme observations are not automatically data errors.",
            "- Distributional summaries do not establish predictive power.",
            "- This block does not evaluate future volatility targets.",
            "",
        ]
    )

    REPORT_PATH.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


# ============================================================
# MAIN
# ============================================================

def run() -> None:
    ensure_dirs()

    df = load_panel()
    features = available_features(df)

    log(f"Features selected: {len(features)}")

    pooled = build_pooled_statistics(df, features)
    ticker_stats = build_ticker_statistics(df, features)
    ticker_summary = summarize_ticker_statistics(ticker_stats)
    extremes = build_extreme_observations(df, features)

    pooled.to_csv(
        TABLE_DIR / "pooled_statistics.csv",
        index=False,
    )

    ticker_stats.to_csv(
        TABLE_DIR / "ticker_statistics.csv",
        index=False,
    )

    ticker_summary.to_csv(
        TABLE_DIR / "ticker_statistics_summary.csv",
        index=False,
    )

    extremes.to_csv(
        TABLE_DIR / "extreme_observations.csv",
        index=False,
    )

    figures = plot_histograms(df, features)
    figures.extend(plot_ticker_boxplots(ticker_stats))

    write_report(
        df,
        pooled,
        ticker_stats,
        ticker_summary,
        extremes,
        figures,
    )

    log("=" * 60)
    log("EDA BLOCK 01 COMPLETE")
    log("=" * 60)
    log(f"Features: {len(features)}")
    log(f"Pooled statistics: {len(pooled)}")
    log(f"Ticker statistics: {len(ticker_stats)}")
    log(f"Extreme observations: {len(extremes)}")
    log(f"Figures: {len(figures)}")
    log(f"Report: {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reserved for compatibility with the future orchestrator.",
    )
    parser.parse_args()

    run()


if __name__ == "__main__":
    main()
