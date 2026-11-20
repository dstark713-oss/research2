
# research2/scripts/eda/02_temporal_dependence.py

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import kendalltau, spearmanr
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import acf


ROOT = Path(__file__).resolve().parents[2]

PANEL_PATH = ROOT / "data" / "derived" / "eda" / "eda_panel.parquet"
OUTPUT_DIR = ROOT / "reports" / "eda" / "02_temporal_dependence"
FIGURES_DIR = OUTPUT_DIR / "figures"
TABLES_DIR = OUTPUT_DIR / "tables"
REPORT_PATH = ROOT / "reports" / "eda" / "02_TEMPORAL_DEPENDENCE.md"

MIN_OBSERVATIONS = 250
MAX_LAG = 40
LJUNG_BOX_LAGS = [5, 10, 20, 40]

SERIES = {
    "return_1d": "Daily returns",
    "abs_return": "Absolute returns",
    "squared_return": "Squared returns",
    "log_volume": "Log volume",
    "log_value": "Log money turnover",
}

DEPENDENCE_SERIES = [
    "return_1d",
    "abs_return",
    "squared_return",
    "log_volume",
    "log_value",
]


def ensure_dirs() -> None:
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_panel() -> pd.DataFrame:
    if not PANEL_PATH.exists():
        raise FileNotFoundError(
            f"Panel not found: {PANEL_PATH}\n"
            "Run 00_build_eda_panel.py first."
        )

    df = pd.read_parquet(PANEL_PATH)

    required = {"secid", "tradedate", *DEPENDENCE_SERIES}
    missing = required.difference(df.columns)

    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df["tradedate"] = pd.to_datetime(df["tradedate"])
    df = df.sort_values(["secid", "tradedate"]).reset_index(drop=True)

    if df.duplicated(["secid", "tradedate"]).any():
        raise ValueError("Panel contains duplicate SECID/date rows.")

    return df


def calculate_acf_for_series(
    values: pd.Series,
    max_lag: int = MAX_LAG,
) -> np.ndarray:
    x = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    x = x[np.isfinite(x)]

    if len(x) < MIN_OBSERVATIONS:
        return np.full(max_lag + 1, np.nan)

    if np.nanstd(x) == 0:
        return np.full(max_lag + 1, np.nan)

    max_allowed = min(max_lag, len(x) - 2)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = acf(
            x,
            nlags=max_allowed,
            fft=True,
            adjusted=False,
            missing="drop",
        )

    output = np.full(max_lag + 1, np.nan)
    output[: len(result)] = result
    return output


def calculate_acf_tables(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ticker_rows = []
    lag_rows = []

    for secid, group in df.groupby("secid", sort=False):
        group = group.sort_values("tradedate")

        for feature in DEPENDENCE_SERIES:
            values = group[feature]
            n = values.notna().sum()

            if n < MIN_OBSERVATIONS:
                continue

            acf_values = calculate_acf_for_series(values)

            for lag, value in enumerate(acf_values):
                if lag == 0:
                    continue

                lag_rows.append(
                    {
                        "secid": secid,
                        "feature": feature,
                        "lag": lag,
                        "acf": value,
                        "n_observations": int(n),
                    }
                )

            ticker_rows.append(
                {
                    "secid": secid,
                    "feature": feature,
                    "n_observations": int(n),
                    "acf_lag_1": acf_values[1],
                    "acf_lag_5": acf_values[5],
                    "acf_lag_10": acf_values[10],
                    "acf_lag_20": acf_values[20],
                    "acf_lag_40": acf_values[40],
                }
            )

    return pd.DataFrame(ticker_rows), pd.DataFrame(lag_rows)


def calculate_ljung_box(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for secid, group in df.groupby("secid", sort=False):
        group = group.sort_values("tradedate")

        for feature in DEPENDENCE_SERIES:
            x = pd.to_numeric(group[feature], errors="coerce")
            x = x.replace([np.inf, -np.inf], np.nan).dropna()

            if len(x) < MIN_OBSERVATIONS:
                continue

            if x.std() == 0:
                continue

            max_lag = min(max(LJUNG_BOX_LAGS), len(x) // 5)
            valid_lags = [lag for lag in LJUNG_BOX_LAGS if lag <= max_lag]

            if not valid_lags:
                continue

            try:
                result = acorr_ljungbox(
                    x,
                    lags=valid_lags,
                    return_df=True,
                )

                for lag, row in result.iterrows():
                    rows.append(
                        {
                            "secid": secid,
                            "feature": feature,
                            "lag": int(lag),
                            "lb_stat": float(row["lb_stat"]),
                            "lb_pvalue": float(row["lb_pvalue"]),
                            "n_observations": len(x),
                        }
                    )
            except Exception as exc:
                rows.append(
                    {
                        "secid": secid,
                        "feature": feature,
                        "lag": np.nan,
                        "lb_stat": np.nan,
                        "lb_pvalue": np.nan,
                        "n_observations": len(x),
                        "error": str(exc),
                    }
                )

    return pd.DataFrame(rows)


def summarize_acf(
    ticker_acf: pd.DataFrame,
    lag_acf: pd.DataFrame,
) -> pd.DataFrame:
    if ticker_acf.empty:
        return pd.DataFrame()

    rows = []

    for feature, group in ticker_acf.groupby("feature"):
        for column in [
            "acf_lag_1",
            "acf_lag_5",
            "acf_lag_10",
            "acf_lag_20",
            "acf_lag_40",
        ]:
            x = group[column].dropna()

            if x.empty:
                continue

            rows.append(
                {
                    "feature": feature,
                    "metric": column,
                    "n_tickers": len(x),
                    "mean": x.mean(),
                    "median": x.median(),
                    "std": x.std(),
                    "q10": x.quantile(0.10),
                    "q25": x.quantile(0.25),
                    "q75": x.quantile(0.75),
                    "q90": x.quantile(0.90),
                    "positive_share": (x > 0).mean(),
                    "negative_share": (x < 0).mean(),
                }
            )

    return pd.DataFrame(rows)


def summarize_ljung_box(lb: pd.DataFrame) -> pd.DataFrame:
    if lb.empty:
        return pd.DataFrame()

    clean = lb.dropna(subset=["lb_pvalue"]).copy()

    if clean.empty:
        return pd.DataFrame()

    clean["significant_5pct"] = clean["lb_pvalue"] < 0.05
    clean["significant_1pct"] = clean["lb_pvalue"] < 0.01

    return (
        clean.groupby(["feature", "lag"])
        .agg(
            n_tickers=("secid", "nunique"),
            significant_share_5pct=("significant_5pct", "mean"),
            significant_share_1pct=("significant_1pct", "mean"),
            median_pvalue=("lb_pvalue", "median"),
            median_lb_stat=("lb_stat", "median"),
        )
        .reset_index()
    )


def calculate_acf_decay(lag_acf: pd.DataFrame) -> pd.DataFrame:
    if lag_acf.empty:
        return pd.DataFrame()

    rows = []

    for (secid, feature), group in lag_acf.groupby(["secid", "feature"]):
        group = group.sort_values("lag")
        values = group["acf"].to_numpy(dtype=float)
        lags = group["lag"].to_numpy(dtype=float)

        valid = np.isfinite(values) & np.isfinite(lags)
        values = values[valid]
        lags = lags[valid]

        if len(values) < 5:
            continue

        positive = values > 0
        positive_lags = lags[positive]
        positive_values = values[positive]

        if len(positive_values) >= 5:
            tau, _ = kendalltau(positive_lags, positive_values)
            rho, _ = spearmanr(positive_lags, positive_values)
        else:
            tau = np.nan
            rho = np.nan

        rows.append(
            {
                "secid": secid,
                "feature": feature,
                "positive_acf_lags": len(positive_values),
                "acf_decay_kendall_tau": tau,
                "acf_decay_spearman_rho": rho,
                "acf_abs_mean": np.nanmean(np.abs(values)),
            }
        )

    return pd.DataFrame(rows)


def calculate_hurst_rs(values: pd.Series) -> float:
    """
    Classical rescaled-range Hurst estimate.

    This is a rough diagnostic, not a formal proof of long memory.
    """
    x = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    x = x[np.isfinite(x)]

    if len(x) < 500:
        return np.nan

    x = x - np.mean(x)

    window_sizes = [
        size for size in [10, 20, 40, 80, 160, 320]
        if size <= len(x) // 4
    ]

    rs_values = []
    used_sizes = []

    for size in window_sizes:
        n_chunks = len(x) // size
        chunk_rs = []

        for i in range(n_chunks):
            chunk = x[i * size : (i + 1) * size]

            if len(chunk) < size:
                continue

            std = np.std(chunk, ddof=1)

            if not np.isfinite(std) or std == 0:
                continue

            cumulative = np.cumsum(chunk - np.mean(chunk))
            r = np.max(cumulative) - np.min(cumulative)
            chunk_rs.append(r / std)

        if chunk_rs:
            rs_values.append(np.mean(chunk_rs))
            used_sizes.append(size)

    if len(rs_values) < 3:
        return np.nan

    slope = np.polyfit(
        np.log(used_sizes),
        np.log(rs_values),
        1,
    )[0]

    return float(slope)


def calculate_hurst_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for secid, group in df.groupby("secid", sort=False):
        group = group.sort_values("tradedate")

        for feature in ["return_1d", "abs_return", "squared_return"]:
            x = group[feature].dropna()

            if len(x) < 500:
                continue

            rows.append(
                {
                    "secid": secid,
                    "feature": feature,
                    "n_observations": len(x),
                    "hurst_rs": calculate_hurst_rs(x),
                }
            )

    return pd.DataFrame(rows)


def plot_acf_curves(lag_acf: pd.DataFrame) -> list[str]:
    outputs = []

    if lag_acf.empty:
        return outputs

    for feature, group in lag_acf.groupby("feature"):
        summary = (
            group.groupby("lag")["acf"]
            .agg(
                median="median",
                q25=lambda x: x.quantile(0.25),
                q75=lambda x: x.quantile(0.75),
            )
            .reset_index()
        )

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.plot(summary["lag"], summary["median"], label="Median ACF")
        ax.fill_between(
            summary["lag"],
            summary["q25"],
            summary["q75"],
            alpha=0.2,
            label="IQR across tickers",
        )
        ax.axhline(0, linewidth=0.8)
        ax.set_title(f"ACF across tickers: {feature}")
        ax.set_xlabel("Lag (trading sessions)")
        ax.set_ylabel("Autocorrelation")
        ax.legend()
        ax.grid(alpha=0.2)

        path = FIGURES_DIR / f"acf_{feature}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

        outputs.append(str(path.relative_to(ROOT)))

    return outputs


def plot_acf_lag_distributions(ticker_acf: pd.DataFrame) -> list[str]:
    outputs = []

    if ticker_acf.empty:
        return outputs

    for column in ["acf_lag_1", "acf_lag_5", "acf_lag_20"]:
        features = [
            feature for feature in ticker_acf["feature"].unique()
            if ticker_acf.loc[
                ticker_acf["feature"] == feature, column
            ].notna().any()
        ]

        if not features:
            continue

        fig, ax = plt.subplots(figsize=(11, 5))

        data = [
            ticker_acf.loc[
                ticker_acf["feature"] == feature, column
            ].dropna().to_numpy()
            for feature in features
        ]

        ax.boxplot(data, tick_labels=features, showfliers=False)
        ax.axhline(0, linewidth=0.8)
        ax.set_title(f"Cross-sectional distribution of {column}")
        ax.set_ylabel("Autocorrelation")
        ax.tick_params(axis="x", rotation=25)
        ax.grid(axis="y", alpha=0.2)

        path = FIGURES_DIR / f"distribution_{column}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

        outputs.append(str(path.relative_to(ROOT)))

    return outputs


def plot_ljung_box(lb_summary: pd.DataFrame) -> list[str]:
    outputs = []

    if lb_summary.empty:
        return outputs

    for lag in sorted(lb_summary["lag"].dropna().unique()):
        subset = lb_summary[lb_summary["lag"] == lag]

        fig, ax = plt.subplots(figsize=(10, 5))

        ax.bar(
            subset["feature"],
            subset["significant_share_5pct"],
        )
        ax.set_ylim(0, 1)
        ax.set_title(f"Ljung-Box: share of tickers significant at 5%, lag={lag}")
        ax.set_ylabel("Share of tickers")
        ax.tick_params(axis="x", rotation=25)
        ax.grid(axis="y", alpha=0.2)

        path = FIGURES_DIR / f"ljung_box_significance_lag_{int(lag)}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)

        outputs.append(str(path.relative_to(ROOT)))

    return outputs


def plot_hurst(hurst: pd.DataFrame) -> list[str]:
    outputs = []

    if hurst.empty:
        return outputs

    fig, ax = plt.subplots(figsize=(10, 5))

    features = list(hurst["feature"].dropna().unique())
    data = [
        hurst.loc[hurst["feature"] == feature, "hurst_rs"].dropna()
        for feature in features
    ]

    ax.boxplot(data, tick_labels=features, showfliers=False)
    ax.axhline(0.5, linestyle="--", linewidth=1, label="H = 0.5")
    ax.set_title("R/S Hurst estimates across tickers")
    ax.set_ylabel("Hurst estimate")
    ax.legend()
    ax.grid(axis="y", alpha=0.2)

    path = FIGURES_DIR / "hurst_rs_distribution.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)

    outputs.append(str(path.relative_to(ROOT)))
    return outputs


def write_report(
    df: pd.DataFrame,
    ticker_acf: pd.DataFrame,
    acf_summary: pd.DataFrame,
    lb_summary: pd.DataFrame,
    decay: pd.DataFrame,
    hurst: pd.DataFrame,
    figures: list[str],
) -> None:
    lines = [
        "# Block 02. Temporal Dependence",
        "",
        "## Research question",
        "",
        "Does the past of each ticker contain measurable temporal dependence "
        "in returns, volatility proxies, volume and money turnover?",
        "",
        "## Dataset",
        "",
        f"- Rows: {len(df):,}",
        f"- Tickers: {df['secid'].nunique():,}",
        f"- First date: {df['tradedate'].min().date()}",
        f"- Last date: {df['tradedate'].max().date()}",
        f"- Minimum observations per ticker-series: {MIN_OBSERVATIONS}",
        f"- Maximum ACF lag: {MAX_LAG}",
        "",
        "## Methodology",
        "",
        "- ACF is calculated independently for each ticker.",
        "- Cross-sectional summaries describe the distribution across tickers.",
        "- Ljung-Box tests are calculated per ticker and feature.",
        "- P-values are descriptive diagnostics; multiple testing is not "
        "corrected in this block.",
        "- Hurst R/S is a rough diagnostic and is not treated as proof "
        "of long memory.",
        "- Missing observations are not forward-filled.",
        "- No observations are winsorized or automatically removed.",
        "",
        "## ACF summary",
        "",
        "The table reports cross-sectional summaries of ticker-level ACF.",
        "",
        "| Feature | Metric | Tickers | Median | Q25 | Q75 | Positive share |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    if acf_summary.empty:
        lines.append("| No valid results | | | | | | |")
    else:
        selected = acf_summary[
            acf_summary["metric"].isin(
                ["acf_lag_1", "acf_lag_5", "acf_lag_20"]
            )
        ]

        for _, row in selected.iterrows():
            lines.append(
                f"| {row['feature']} | {row['metric']} | "
                f"{int(row['n_tickers'])} | {row['median']:.4f} | "
                f"{row['q25']:.4f} | {row['q75']:.4f} | "
                f"{row['positive_share']:.3f} |"
            )

    lines += [
        "",
        "## Ljung-Box summary",
        "",
        "Share of ticker-level tests with p-value below 0.05. "
        "These are unadjusted p-values.",
        "",
        "| Feature | Lag | Tickers | Significant share | Median p-value |",
        "|---|---:|---:|---:|---:|",
    ]

    if lb_summary.empty:
        lines.append("| No valid results | | | | |")
    else:
        for _, row in lb_summary.iterrows():
            lines.append(
                f"| {row['feature']} | {int(row['lag'])} | "
                f"{int(row['n_tickers'])} | "
                f"{row['significant_share_5pct']:.3f} | "
                f"{row['median_pvalue']:.6g} |"
            )

    lines += [
        "",
        "## ACF decay diagnostics",
        "",
        "Kendall and Spearman coefficients describe the relationship "
        "between lag and positive ACF values. They are descriptive only.",
        "",
    ]

    if decay.empty:
        lines.append("No valid decay estimates.")
    else:
        lines.append(
            f"- Ticker-series observations: {len(decay):,}"
        )
        lines.append(
            f"- Median Kendall tau: "
            f"{decay['acf_decay_kendall_tau'].median():.4f}"
        )
        lines.append(
            f"- Median Spearman rho: "
            f"{decay['acf_decay_spearman_rho'].median():.4f}"
        )

    lines += [
        "",
        "## Hurst R/S diagnostics",
        "",
    ]

    if hurst.empty:
        lines.append("No valid Hurst estimates.")
    else:
        for feature, group in hurst.groupby("feature"):
            values = group["hurst_rs"].dropna()
            if values.empty:
                continue

            lines.append(
                f"- **{feature}:** n={len(values)}, "
                f"median={values.median():.4f}, "
                f"Q25={values.quantile(0.25):.4f}, "
                f"Q75={values.quantile(0.75):.4f}"
            )

    lines += [
        "",
        "## Figures",
        "",
    ]

    for figure in figures:
        lines.append(f"![{Path(figure).stem}]({Path(figure).relative_to('reports/eda')})")
        lines.append("")

    lines += [
        "## Limitations",
        "",
        "- ACF is calculated on the sequence of available non-null observations. "
        "If a ticker has missing market sessions, the lag index is an observation "
        "lag rather than necessarily a consecutive market-session lag.",
        "- Ljung-Box p-values are not corrected for multiple testing.",
        "- Cross-sectional medians can hide substantial ticker heterogeneity.",
        "- R/S Hurst estimates are sensitive to short samples, structural breaks "
        "and non-stationarity.",
        "- This block does not establish predictive usefulness or causal effects.",
        "",
        "## Outputs",
        "",
        "- `tables/ticker_acf.csv`",
        "- `tables/acf_by_lag.csv`",
        "- `tables/acf_summary.csv`",
        "- `tables/ljung_box.csv`",
        "- `tables/ljung_box_summary.csv`",
        "- `tables/acf_decay.csv`",
        "- `tables/hurst_rs.csv`",
        "",
        "## Status",
        "",
        "Descriptive temporal-dependence analysis only. No model or strategy "
        "conclusions are drawn in this report.",
    ]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run_smoke_tests(
    df: pd.DataFrame,
    ticker_acf: pd.DataFrame,
    lag_acf: pd.DataFrame,
    lb: pd.DataFrame,
    hurst: pd.DataFrame,
) -> dict:
    checks = {
        "panel_not_empty": len(df) > 0,
        "unique_ticker_date": not df.duplicated(["secid", "tradedate"]).any(),
        "acf_output_created": not ticker_acf.empty,
        "acf_lag_output_created": not lag_acf.empty,
        "ljung_box_output_created": not lb.empty,
        "hurst_output_created": not hurst.empty,
        "acf_values_finite_or_null": (
            ticker_acf.select_dtypes(include=[np.number])
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            .any()
            .all()
        ) if not ticker_acf.empty else False,
    }

    failed = [name for name, passed in checks.items() if not passed]

    result = {
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failed_checks": failed,
        "panel_rows": len(df),
        "tickers": int(df["secid"].nunique()),
        "acf_ticker_series": len(ticker_acf),
        "acf_lag_rows": len(lag_acf),
        "ljung_box_rows": len(lb),
        "hurst_rows": len(hurst),
    }

    (OUTPUT_DIR / "smoke_test.json").write_text(
        json.dumps(
            result,
            indent=2,
            default=lambda value: (
                value.item()
                if isinstance(value, np.generic)
                else str(value)
            ),
        ),
        encoding="utf-8",
    )

    return result


def main() -> None:
    ensure_dirs()

    print("[1/8] Loading cached panel...")
    df = load_panel()

    print("[2/8] Calculating ticker-level ACF...")
    ticker_acf, lag_acf = calculate_acf_tables(df)

    print("[3/8] Calculating Ljung-Box tests...")
    lb = calculate_ljung_box(df)

    print("[4/8] Building summaries...")
    acf_summary = summarize_acf(ticker_acf, lag_acf)
    lb_summary = summarize_ljung_box(lb)
    decay = calculate_acf_decay(lag_acf)

    print("[5/8] Calculating Hurst R/S diagnostics...")
    hurst = calculate_hurst_table(df)

    print("[6/8] Saving tables...")
    outputs = {
        "ticker_acf.csv": ticker_acf,
        "acf_by_lag.csv": lag_acf,
        "acf_summary.csv": acf_summary,
        "ljung_box.csv": lb,
        "ljung_box_summary.csv": lb_summary,
        "acf_decay.csv": decay,
        "hurst_rs.csv": hurst,
    }

    for filename, table in outputs.items():
        table.to_csv(TABLES_DIR / filename, index=False)

    print("[7/8] Plotting...")
    figures = []
    figures += plot_acf_curves(lag_acf)
    figures += plot_acf_lag_distributions(ticker_acf)
    figures += plot_ljung_box(lb_summary)
    figures += plot_hurst(hurst)

    print("[8/8] Writing report and running smoke tests...")
    write_report(
        df,
        ticker_acf,
        acf_summary,
        lb_summary,
        decay,
        hurst,
        figures,
    )

    smoke = run_smoke_tests(
        df,
        ticker_acf,
        lag_acf,
        lb,
        hurst,
    )

    print("\nBLOCK 02 COMPLETE")
    print(json.dumps(
        smoke,
        indent=2,
        default=lambda value: value.item()
        if isinstance(value, np.generic)
        else str(value),
    ))


if __name__ == "__main__":
    main()
