"""Synthetic but realistic daily retail sales.

Five stores x four product categories, two full years of daily unit sales.
Each series combines the patterns real retail data has: a store and category
base level, slow growth, a weekly cycle (busier weekends), yearly seasonality
that differs by category, planned promotions that lift sales, holiday spikes
(Black Friday, the run-up to Christmas) and Poisson-gamma noise. Generating the
data keeps the project self-contained, with no download or private dataset.
"""

import numpy as np
import pandas as pd

STORES = {"Milan": 1.35, "Rome": 1.2, "Turin": 0.95, "Bologna": 0.85, "Ancona": 0.65}
CATEGORIES = {
    # base units/day, unit price (EUR), peak month, seasonal amplitude
    "Electronics": (38, 120.0, 12, 0.35),
    "Home & Kitchen": (55, 35.0, 11, 0.18),
    "Clothing": (80, 28.0, 4, 0.25),
    "Sports & Outdoors": (42, 45.0, 6, 0.40),
}
PRICES = {c: v[1] for c, v in CATEGORIES.items()}
START, END = "2024-01-01", "2025-12-31"


def holiday_lift(dates: pd.DatetimeIndex) -> np.ndarray:
    """Multiplicative lift for Black Friday weekend and the weeks before Christmas."""
    lift = np.ones(len(dates))
    for year in sorted(set(dates.year)):
        nov = pd.date_range(f"{year}-11-01", f"{year}-11-30")
        black_friday = nov[(nov.day_of_week == 4)][3]  # fourth Friday of November
        bf = (dates >= black_friday) & (dates <= black_friday + pd.Timedelta(days=3))
        lift[bf] *= 1.9
        xmas = (dates >= f"{year}-12-10") & (dates <= f"{year}-12-23")
        lift[xmas] *= 1.45
        closed = (dates == f"{year}-12-25") | (dates == f"{year}-01-01")
        lift[closed] *= 0.05
    return lift


def is_holiday_period(dates: pd.DatetimeIndex) -> np.ndarray:
    return (holiday_lift(dates) != 1).astype(int)


def promo_calendar(dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Planned promotions: a few 3-7 day campaigns per quarter."""
    promo = np.zeros(len(dates), dtype=int)
    n_campaigns = max(1, len(dates) // 45)
    for start in rng.choice(len(dates) - 7, n_campaigns, replace=False):
        promo[start : start + rng.integers(3, 8)] = 1
    return promo


def generate(seed: int = 7, start: str = START, end: str = END) -> pd.DataFrame:
    """Return one row per date, store and category with units sold and revenue."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, end, freq="D")
    t = np.arange(len(dates)) / 365.0
    weekday = np.array([0.9, 0.85, 0.88, 0.95, 1.1, 1.35, 1.0])[dates.day_of_week]
    holidays = holiday_lift(dates)

    frames = []
    for store, store_mult in STORES.items():
        growth = rng.uniform(0.03, 0.12)
        for cat, (base, price, peak, amp) in CATEGORIES.items():
            promo = promo_calendar(dates, rng)
            promo_lift = rng.uniform(1.25, 1.55)
            yearly = 1 + amp * np.cos(2 * np.pi * (dates.month - peak) / 12)
            mean = base * store_mult * (1 + growth) ** t * weekday * yearly * holidays * np.where(promo, promo_lift, 1)
            # Gamma-Poisson (negative binomial) noise, like real count data
            units = rng.poisson(rng.gamma(shape=12, scale=mean / 12))
            frames.append(
                pd.DataFrame(
                    {
                        "date": dates,
                        "store": store,
                        "category": cat,
                        "promo": promo,
                        "units": units,
                        "revenue": units * price * np.where(promo, 0.85, 1.0),
                    }
                )
            )
    return pd.concat(frames, ignore_index=True)
