
# Path: scripts/alt_view/normality_diagnostics.py
# Description: Normality diagnostics for Research2 daily return series.

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats


ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INPUT = (
    ROOT
    / "data"
    / "reports"
    / "risk_control"
    / "daily_returns.csv"
)

DEFAULT_OUTPUT = (
    ROOT
    / "data"
    / "reports"
    / "alt_view"
    / "normality"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Normality diagnostics for Research2 returns."
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )

    parser.add_argument(
        "--min-n",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--columns",
        nargs="*",
        default=None,
        help="Optional list of columns to analyze.",
    )

    return parser.parse_args()


def safe_filename(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name))


def load_data(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Input file not found: {path}"
        )

    df = pd.read_csv(path, encoding="utf-8-sig")

    date_candidates = [
        "tradedate",
        "TRADEDATE",
        "date",
        "Date",
        "DATE",
    ]

    date_col = next(
        (c for c in date_candidates if c in df.columns),
        None,
    )

    if date_col is not None:
        df[date_col] = pd.to_datetime(
            df[date_col],
            errors="coerce",
        )

        df = df.sort_values(date_col)

    return df, date_col


def prepare_series(df, date_col, selected_columns=None):
    numeric_columns = df.select_dtypes(
        include=[np.number]
    ).columns.tolist()

    if selected_columns:
        missing = [
            c for c in selected_columns
            if c not in numeric_columns
        ]

        if missing:
            raise ValueError(
                f"Requested columns are not numeric or absent: {missing}"
            )

        numeric_columns = selected_columns

    if not numeric_columns:
        raise RuntimeError("No numeric columns found.")

    series_dict = {}

    for column in numeric_columns:
        values = pd.to_numeric(
            df[column],
            errors="coerce",
        )

        values = values.replace(
            [np.inf, -np.inf],
            np.nan,
        ).dropna()

        series_dict[column] = values.to_numpy(dtype=float)

    return series_dict


def run_tests(name, x, min_n):
    n = len(x)

    result = {
        "series": name,
        "n": n,
        "mean": np.mean(x),
        "median": np.median(x),
        "std": np.std(x, ddof=1),
        "variance": np.var(x, ddof=1),
        "min": np.min(x),
        "max": np.max(x),
        "skewness": stats.skew(x, bias=False),
        "excess_kurtosis": stats.kurtosis(
            x,
            fisher=True,
            bias=False,
        ),
    }

    if n < min_n:
        result["status"] = f"SKIPPED: n < {min_n}"
        return result

    result["status"] = "OK"

    # Shapiro-Wilk
    shapiro = stats.shapiro(x)

    result["shapiro_w"] = shapiro.statistic
    result["shapiro_p"] = shapiro.pvalue

    # D'Agostino-Pearson K-squared
    dagostino = stats.normaltest(x)

    result["dagostino_k2"] = dagostino.statistic
    result["dagostino_p"] = dagostino.pvalue

    # Jarque-Bera
    jb = stats.jarque_bera(x)

    result["jarque_bera"] = jb.statistic
    result["jarque_bera_p"] = jb.pvalue

    # Anderson-Darling
    ad = stats.anderson(x, dist="norm")

    levels = np.asarray(ad.significance_level)
    critical = np.asarray(ad.critical_values)

    idx_5 = int(np.argmin(np.abs(levels - 5.0)))

    result["anderson_stat"] = ad.statistic
    result["anderson_critical_5pct"] = critical[idx_5]
    result["anderson_reject_5pct"] = (
        ad.statistic > critical[idx_5]
    )

    # Standardized observations outside +/- 3 sample SD.
    z = (x - np.mean(x)) / np.std(x, ddof=1)

    result["outliers_abs_z_gt_2"] = int(
        np.sum(np.abs(z) > 2)
    )

    result["outliers_abs_z_gt_3"] = int(
        np.sum(np.abs(z) > 3)
    )

    return result


def create_qq_plot(name, x, output_dir):
    fig, ax = plt.subplots(figsize=(10, 7))

    fig.patch.set_facecolor("#101010")
    ax.set_facecolor("#101010")

    stats.probplot(
        x,
        dist="norm",
        plot=ax,
    )

    # Sample quantiles
    ax.get_lines()[0].set_color("#A0A0A0")
    ax.get_lines()[0].set_marker("o")
    ax.get_lines()[0].set_markersize(3)
    ax.get_lines()[0].set_markerfacecolor("#A0A0A0")
    ax.get_lines()[0].set_markeredgecolor("#A0A0A0")
    ax.get_lines()[0].set_linestyle("none")

    # Theoretical normal reference line
    ax.get_lines()[1].set_color("#666666")
    ax.get_lines()[1].set_linewidth(1.5)

    ax.set_title(
        f"QQ-Plot | {name} | n={len(x)}",
        color="#D0D0D0",
        fontsize=13,
    )

    ax.set_xlabel(
        "Theoretical normal quantiles",
        color="#A0A0A0",
    )

    ax.set_ylabel(
        "Sample quantiles",
        color="#A0A0A0",
    )

    ax.tick_params(colors="#A0A0A0")

    for spine in ax.spines.values():
        spine.set_color("#555555")

    ax.grid(
        True,
        color="#333333",
        linestyle="--",
        linewidth=0.6,
        alpha=0.7,
    )

    fig.tight_layout()

    filename = safe_filename(name) + ".png"

    fig.savefig(
        output_dir / filename,
        dpi=160,
        facecolor=fig.get_facecolor(),
    )

    plt.close(fig)


def main():
    args = parse_args()

    qq_dir = args.output / "qq_plots"
    qq_dir.mkdir(parents=True, exist_ok=True)

    df, date_col = load_data(args.input)

    series_dict = prepare_series(
        df,
        date_col,
        args.columns,
    )

    print(f"Input: {args.input}")
    print(f"Rows in source: {len(df):,}")
    print(f"Date column: {date_col}")
    print(f"Series found: {len(series_dict)}")

    results = []

    for name, x in series_dict.items():
        print(f"\nAnalyzing: {name}")

        result = run_tests(
            name,
            x,
            args.min_n,
        )

        results.append(result)

        if result["status"] == "OK":
            create_qq_plot(
                name,
                x,
                qq_dir,
            )

            print(
                f"n={result['n']}, "
                f"mean={result['mean']:.6f}, "
                f"std={result['std']:.6f}"
            )

            print(
                f"Shapiro p={result['shapiro_p']:.6g}; "
                f"JB p={result['jarque_bera_p']:.6g}; "
                f"AD reject 5%={result['anderson_reject_5pct']}"
            )

    report = pd.DataFrame(results)

    report_path = (
        args.output / "normality_tests.csv"
    )

    report.to_csv(
        report_path,
        index=False,
        float_format="%.10g",
    )

    print("\nRESULTS")
    print(
        report[
            [
                "series",
                "n",
                "skewness",
                "excess_kurtosis",
                "shapiro_p",
                "dagostino_p",
                "jarque_bera_p",
                "anderson_reject_5pct",
            ]
        ].to_string(index=False)
    )

    print(f"\nReport saved: {report_path}")
    print(f"QQ plots saved: {qq_dir}")


if __name__ == "__main__":
    main()
