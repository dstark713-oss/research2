# Path: scripts/risk/config.py
# Description: Configuration for the first portfolio risk-control experiment.

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data" / "market_data" / "moex_daily_adjusted.sqlite"
OUTPUT_DIR = ROOT / "data" / "reports" / "risk_control"

TICKERS = [
    "GAZP",
    "LKOH",
    "SBER",
    "ROSN",
    "GMKN",
    "NVTK",
    "MGNT",
    "IRAO",
]

APPROVED_BOARDS = ("TQBR", "EQBR")

TRADING_DAYS = 252

# Shared initial parameters.
REBALANCE_FREQUENCY = "M"
VOL_WINDOW = 20
CORR_WINDOW = 60

# Annualized volatility parameters.
PORTFOLIO_VOL_TARGET = 0.15
INDIVIDUAL_VOL_CAP = 0.20

# Correlation parameters.
CORR_EXPOSURE_FLOOR = 0.30
CORR_EXPOSURE_CEILING = 0.80
CORR_MIN_EXPOSURE = 0.25

CORR_WEIGHT_LAMBDA = 2.0
CORR_WEIGHT_MAX = 0.30

# Inverse volatility maximum individual weight.
INVERSE_VOL_MAX_WEIGHT = 0.40

# R06 regime thresholds.
VOL_REGIME_MEDIUM = 1.50
VOL_REGIME_HIGH = 2.00

# Initial transaction cost assumption.
COST_BPS = 10.0