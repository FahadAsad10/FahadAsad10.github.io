"""Demand forecasting with gradient boosting, plus backtesting and a reorder plan.

The model is a single "global" HistGradientBoostingRegressor trained on all
store x category series at once. It forecasts the next HORIZON days in one shot
(a direct strategy): every lag feature is at least HORIZON days old, so the same
features exist for every day of the forecast window and no prediction is fed
back in as an input. Two extra quantile models give a P10-P90 band, which is
what turns a forecast into safety stock.
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from sales_forecaster.data import CATEGORIES, PRICES, STORES

HORIZON = 28
LAGS = [28, 35, 42, 49, 364]
CALENDAR = [
    "dow",
    "month",
    "doy_sin",
    "doy_cos",
    "black_friday",
    "xmas_run",
    "closed",
    "promo",
]
FEATURES = (
    [f"lag_{k}" for k in LAGS]
    + ["dow_mean", "roll_28", "roll_7", "trend_ratio"]
    + CALENDAR
    + [f"store_{s}" for s in STORES]
    + [f"cat_{c}" for c in CATEGORIES]
)
KEYS = ["store", "category"]


def _calendar(dates: pd.Series) -> pd.DataFrame:
    d = pd.DatetimeIndex(dates)
    out = pd.DataFrame(index=dates.index)
    out["dow"] = d.day_of_week
    out["month"] = d.month
    out["doy_sin"] = np.sin(2 * np.pi * d.day_of_year / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * d.day_of_year / 365.25)
    bf = np.zeros(len(d), dtype=int)
    for year in sorted(set(d.year)):
        nov = pd.date_range(f"{year}-11-01", f"{year}-11-30")
        friday = nov[nov.day_of_week == 4][3]
        bf |= ((d >= friday) & (d <= friday + pd.Timedelta(days=3))).astype(int)
    out["black_friday"] = bf
    out["xmas_run"] = ((d.month == 12) & (d.day >= 10) & (d.day <= 23)).astype(int)
    out["closed"] = (((d.month == 12) & (d.day == 25)) | ((d.month == 1) & (d.day == 1))).astype(int)
    return out


def make_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Add model features to a panel of date/store/category/promo/units rows.

    Future rows can be included with units = NaN; their lag features are filled
    from history because every lag is at least HORIZON days.
    """
    df = panel.sort_values(KEYS + ["date"]).reset_index(drop=True)
    g = df.groupby(KEYS, sort=False)["units"]
    for k in LAGS:
        df[f"lag_{k}"] = g.shift(k)
    df["dow_mean"] = df[["lag_28", "lag_35", "lag_42", "lag_49"]].mean(axis=1)
    shifted = g.shift(HORIZON)
    df["roll_28"] = shifted.groupby([df[k] for k in KEYS]).transform(lambda s: s.rolling(28, min_periods=14).mean())
    df["roll_7"] = shifted.groupby([df[k] for k in KEYS]).transform(lambda s: s.rolling(7, min_periods=4).mean())
    df["trend_ratio"] = df["roll_28"] / (
        df["lag_364"].groupby([df[k] for k in KEYS]).transform(lambda s: s.rolling(28, min_periods=14).mean()) + 1
    )
    df = pd.concat([df, _calendar(df["date"])], axis=1)
    for s in STORES:
        df[f"store_{s}"] = (df["store"] == s).astype(int)
    for c in CATEGORIES:
        df[f"cat_{c}"] = (df["category"] == c).astype(int)
    return df


def _model(loss: str, quantile: float | None = None) -> HistGradientBoostingRegressor:
    kwargs = {"quantile": quantile} if quantile is not None else {}
    return HistGradientBoostingRegressor(
        loss=loss,
        learning_rate=0.06,
        max_iter=400,
        max_leaf_nodes=31,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=0,
        **kwargs,
    )


def fit(history: pd.DataFrame) -> dict:
    """Fit the point (P50) model and the P10 / P90 quantile models."""
    feats = make_features(history).dropna(subset=["units", "roll_28"])
    X, y = feats[FEATURES], feats["units"]
    return {
        "p50": _model("poisson").fit(X, y),
        "p10": _model("quantile", 0.1).fit(X, y),
        "p90": _model("quantile", 0.9).fit(X, y),
    }


def future_frame(history: pd.DataFrame, promo_days: dict | None = None, horizon: int = HORIZON) -> pd.DataFrame:
    """Rows for the next `horizon` days. promo_days maps (store, category) -> list of dates on promotion."""
    last = history["date"].max()
    dates = pd.date_range(last + pd.Timedelta(days=1), periods=horizon)
    idx = pd.MultiIndex.from_product([dates, STORES, CATEGORIES], names=["date", "store", "category"])
    fut = idx.to_frame(index=False)
    fut["promo"] = 0
    fut["units"] = np.nan
    for (store, cat), days in (promo_days or {}).items():
        mask = (fut["store"] == store) & (fut["category"] == cat) & fut["date"].isin(pd.to_datetime(days))
        fut.loc[mask, "promo"] = 1
    return fut


def predict(models: dict, history: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
    """Forecast every future row; returns date/store/category with p10, p50, p90 units and revenue."""
    cols = ["date", "store", "category", "promo", "units"]
    feats = make_features(pd.concat([history[cols], future[cols]], ignore_index=True))
    feats = feats[feats["date"] > history["date"].max()].copy()
    X = feats[FEATURES]
    p50 = models["p50"].predict(X)
    widen = models.get("widen", 1.0)  # calibrated on the backtest so the band really covers ~80%
    lo = p50 - widen * np.clip(p50 - models["p10"].predict(X), 0, None)
    hi = p50 + widen * np.clip(models["p90"].predict(X) - p50, 0, None)
    out = feats[["date", "store", "category", "promo"]].copy()
    out["p10"], out["p50"], out["p90"] = np.clip(lo, 0, None), p50, hi
    price = out["category"].map(PRICES) * np.where(out["promo"] == 1, 0.85, 1.0)
    out["revenue"] = out["p50"] * price
    return out.reset_index(drop=True)


def wape(actual: np.ndarray, forecast: np.ndarray) -> float:
    """Weighted absolute percentage error: total absolute error / total actual units."""
    return float(np.abs(actual - forecast).sum() / actual.sum())


def backtest(data: pd.DataFrame, folds: int = 6, horizon: int = HORIZON) -> dict:
    """Rolling-origin backtest: refit at each cutoff, forecast the next `horizon` days, compare to baselines."""
    end = data["date"].max()
    rows = []
    for i in range(folds, 0, -1):
        cutoff = end - pd.Timedelta(days=horizon * i)
        hist = data[data["date"] <= cutoff]
        actual = data[(data["date"] > cutoff) & (data["date"] <= cutoff + pd.Timedelta(days=horizon))]
        fut = actual[["date", "store", "category", "promo"]].assign(units=np.nan)
        fc = predict(fit(hist), hist, fut).merge(
            actual[["date", "store", "category", "units"]],
            on=["date", "store", "category"],
        )
        feats = make_features(pd.concat([hist, fut], ignore_index=True))
        feats = feats[feats["date"] > cutoff][["date", "store", "category", "dow_mean", "lag_364"]]
        fc = fc.merge(feats, on=["date", "store", "category"])
        fc["fold"] = str(cutoff.date())
        rows.append(fc)
    res = pd.concat(rows, ignore_index=True)
    y = res["units"].to_numpy()
    summary = {
        "Gradient boosting (this model)": wape(y, res["p50"].to_numpy()),
        "Same weekday, last 4 weeks": wape(y, res["dow_mean"].to_numpy()),
        "Same day last year": wape(y, res["lag_364"].fillna(res["dow_mean"]).to_numpy()),
    }
    by_cat = {
        c: {
            "model": wape(g["units"].to_numpy(), g["p50"].to_numpy()),
            "naive": wape(g["units"].to_numpy(), g["dow_mean"].to_numpy()),
        }
        for c, g in res.groupby("category")
    }
    res["week"] = (res["date"] - res.groupby("fold")["date"].transform("min")).dt.days // 7
    wk = res.groupby(["fold", "week", "store", "category"])[["units", "p50", "dow_mean"]].sum()
    weekly = {
        "model": wape(wk["units"].to_numpy(), wk["p50"].to_numpy()),
        "naive": wape(wk["units"].to_numpy(), wk["dow_mean"].to_numpy()),
    }
    coverage = float(((res["units"] >= res["p10"]) & (res["units"] <= res["p90"])).mean())
    widen = calibrate_band(res)
    bias = float(res["p50"].sum() / res["units"].sum() - 1)
    return {
        "wape": summary,
        "weekly_wape": weekly,
        "by_category": by_cat,
        "coverage_p10_p90_raw": coverage,
        "band_widen": widen,
        "bias": bias,
        "folds": folds,
        "horizon": horizon,
    }, res


def calibrate_band(res: pd.DataFrame, target: float = 0.8) -> float:
    """Smallest multiplier on the P10/P90 gaps that makes the band cover `target` of backtest actuals."""
    lo_gap, hi_gap = res["p50"] - res["p10"], res["p90"] - res["p50"]
    for f in np.arange(1.0, 3.01, 0.05):
        if ((res["units"] >= res["p50"] - f * lo_gap) & (res["units"] <= res["p50"] + f * hi_gap)).mean() >= target:
            return round(float(f), 2)
    return 3.0


def reorder_plan(
    forecast: pd.DataFrame,
    on_hand: pd.DataFrame,
    lead_time: int = 7,
    review_days: int = 14,
) -> pd.DataFrame:
    """How much to order now so stock lasts until the next delivery after this one.

    Cover = lead time + review period. Expected demand is the sum of daily P50s;
    safety stock combines the daily P90-P50 gaps as if days were independent
    (square root of the sum of squares), which approximates a 90% service level.
    """
    cover = lead_time + review_days
    first = forecast["date"].min()
    window = forecast[forecast["date"] < first + pd.Timedelta(days=cover)]
    agg = window.groupby(KEYS).agg(
        expected_demand=("p50", "sum"),
        safety_stock=("p90", lambda s: 0.0),
    )
    gaps = (window["p90"] - window["p50"]) ** 2
    agg["safety_stock"] = np.sqrt(gaps.groupby([window[k] for k in KEYS]).sum())
    plan = agg.reset_index().merge(on_hand, on=KEYS, how="left").fillna({"on_hand": 0})
    plan["target_stock"] = plan["expected_demand"] + plan["safety_stock"]
    plan["order_units"] = np.ceil(np.clip(plan["target_stock"] - plan["on_hand"], 0, None)).astype(int)
    plan["order_value_eur"] = plan["order_units"] * plan["category"].map(PRICES) * 0.55  # cost ~55% of shelf price
    plan["days_of_cover_now"] = plan["on_hand"] / (plan["expected_demand"] / cover)
    plan["stockout_risk"] = np.where(
        plan["on_hand"] < plan["expected_demand"],
        "High",
        np.where(plan["on_hand"] < plan["target_stock"], "Medium", "Low"),
    )
    cols = KEYS + [
        "on_hand",
        "days_of_cover_now",
        "expected_demand",
        "safety_stock",
        "order_units",
        "order_value_eur",
        "stockout_risk",
    ]
    return plan[cols].round(
        {
            "expected_demand": 0,
            "safety_stock": 0,
            "days_of_cover_now": 1,
            "order_value_eur": 0,
        }
    )


def sample_on_hand(data: pd.DataFrame, seed: int = 3) -> pd.DataFrame:
    """Plausible current stock levels: 4-25 days of recent average sales."""
    rng = np.random.default_rng(seed)
    recent = data[data["date"] > data["date"].max() - pd.Timedelta(days=28)].groupby(KEYS)["units"].mean().reset_index()
    recent["on_hand"] = (recent["units"] * rng.uniform(4, 25, len(recent))).round().astype(int)
    return recent[KEYS + ["on_hand"]]


def save(models: dict, metrics: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, directory / "models.joblib")
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2))


def load(directory: Path) -> tuple[dict, dict]:
    return joblib.load(directory / "models.joblib"), json.loads((directory / "metrics.json").read_text())
