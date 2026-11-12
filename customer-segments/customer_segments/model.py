"""RFM segmentation, k-means clustering and customer lifetime value (CLV) prediction.

Three complementary views of the same customers:

1. RFM segments: recency, frequency and monetary scores (1-5) mapped to named
   segments a marketing team can act on (Champions, At risk, Lost, ...).
2. K-means clusters on log-scaled RFM, with k chosen by silhouette score, to
   check the rule-based segments against what the data groups on its own.
3. CLV: gradient boosting predicts each customer's spend over the next 180 days
   and the chance they buy again, validated out of time against the usual
   "rank by past spend" approach.
"""

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from customer_segments.data import CHANNELS, COUNTRIES

WINDOW = 180  # days ahead the CLV model predicts

SEGMENT_ORDER = [
    "Champions",
    "Loyal",
    "Potential loyalists",
    "New customers",
    "Need attention",
    "At risk",
    "Can't lose them",
    "Hibernating",
    "Lost",
]
PLAYBOOK = {
    "Champions": (
        "Bought recently, buy often and spend the most.",
        "Reward them: early access, referral programme, no discounts needed.",
        "Email, community",
    ),
    "Loyal": ("Spend well and buy regularly.", "Upsell higher-value products and bundles; ask for reviews.", "Email"),
    "Potential loyalists": (
        "Recent customers with a few orders.",
        "Membership or loyalty points to build the habit.",
        "Email, retargeting",
    ),
    "New customers": (
        "First order very recently.",
        "Onboarding series and a second-order incentive within 30 days.",
        "Email",
    ),
    "Need attention": (
        "Average on every measure and starting to slip.",
        "Personalised recommendations and limited-time offers.",
        "Email, paid social",
    ),
    "At risk": (
        "Used to buy often, but not for a while.",
        "Win-back campaign with a meaningful offer before they leave.",
        "Email, SMS",
    ),
    "Can't lose them": (
        "Big spenders who have gone quiet.",
        "Personal outreach and the best offer; ask what changed.",
        "Personal email, phone",
    ),
    "Hibernating": ("Low spend, long gone.", "Low-cost reactivation only; exclude from paid campaigns.", "Email"),
    "Lost": ("One small order, long ago.", "Don't spend on them; suppress from paid audiences.", "None"),
}


# ---------- RFM ----------
def rfm_table(orders: pd.DataFrame, customers: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """Per-customer recency, frequency, monetary and behaviour features as of `snapshot` (orders before it only)."""
    past = orders[orders["date"] <= snapshot]
    g = past.groupby("customer_id")
    t = pd.DataFrame(
        {
            "last_order": g["date"].max(),
            "first_order": g["date"].min(),
            "frequency": g.size(),
            "monetary": g["amount"].sum(),
            "discount_share": g["discounted"].mean(),
            "items_per_order": g["items"].mean(),
        }
    )
    t["recency_days"] = (snapshot - t["last_order"]).dt.days
    t["tenure_days"] = (snapshot - t["first_order"]).dt.days
    t["aov"] = t["monetary"] / t["frequency"]
    t["avg_gap_days"] = np.where(t["frequency"] > 1, t["tenure_days"] - t["recency_days"], np.nan) / (
        t["frequency"] - 1
    ).replace(0, np.nan)
    for days in (90, 180):
        recent = past[past["date"] > snapshot - pd.Timedelta(days=days)].groupby("customer_id")["amount"]
        t[f"spend_{days}d"] = recent.sum().reindex(t.index, fill_value=0)
        t[f"orders_{days}d"] = recent.size().reindex(t.index, fill_value=0)
    # How overdue the next order is, relative to the customer's own rhythm (a strong churn signal)
    t["overdue_ratio"] = t["recency_days"] / t["avg_gap_days"]
    t["recent_share"] = t["spend_180d"] / t["monetary"]
    t = t.join(customers.set_index("customer_id")[["channel", "country"]])
    return t.drop(columns=["last_order", "first_order"])


def _quintile(s: pd.Series, reverse: bool = False) -> pd.Series:
    score = pd.qcut(s.rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
    return 6 - score if reverse else score


def score_rfm(rfm: pd.DataFrame) -> pd.DataFrame:
    """Add R, F, M scores (1-5, 5 is best) and the named segment."""
    out = rfm.copy()
    out["R"] = _quintile(out["recency_days"], reverse=True)
    out["F"] = _quintile(out["frequency"])
    out["M"] = _quintile(out["monetary"])
    fm = (out["F"] + out["M"]) / 2
    r = out["R"]
    out["segment"] = np.select(
        [
            (r >= 4) & (fm >= 4),
            (r >= 3) & (fm >= 3.5),
            (r >= 4) & (out["frequency"] == 1),
            (r >= 4) & (fm >= 2),
            (r <= 2) & (fm >= 4),
            (r <= 2) & (fm >= 3),
            (r == 3),
            (r == 2),
        ],
        [
            "Champions",
            "Loyal",
            "New customers",
            "Potential loyalists",
            "Can't lose them",
            "At risk",
            "Need attention",
            "Hibernating",
        ],
        default="Lost",
    )
    # Recent, but low spend and frequency: still worth nurturing
    out.loc[(r >= 4) & (out["segment"] == "Lost"), "segment"] = "Need attention"
    return out


def segment_summary(scored: pd.DataFrame, value_col: str | None = None) -> pd.DataFrame:
    agg = {
        "customers": ("recency_days", "size"),
        "revenue": ("monetary", "sum"),
        "avg_recency_days": ("recency_days", "mean"),
        "avg_orders": ("frequency", "mean"),
        "avg_order_value": ("aov", "mean"),
    }
    if value_col:
        agg["predicted_180d_value"] = (value_col, "sum")
    s = scored.groupby("segment").agg(**agg).reindex(SEGMENT_ORDER).dropna(how="all")
    s["share_of_customers"] = s["customers"] / s["customers"].sum()
    s["share_of_revenue"] = s["revenue"] / s["revenue"].sum()
    s["action"] = [PLAYBOOK[i][1] for i in s.index]
    s["channel"] = [PLAYBOOK[i][2] for i in s.index]
    return s.reset_index()


# ---------- k-means ----------
def cluster_matrix(rfm: pd.DataFrame) -> np.ndarray:
    return StandardScaler().fit_transform(np.log1p(rfm[["recency_days", "frequency", "monetary"]]))


def choose_k(X: np.ndarray, ks=range(3, 8), seed: int = 0) -> dict[int, float]:
    """Silhouette score for each k (on a fixed sample for speed). k starts at 3: two groups are too coarse to act on."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), min(len(X), 2500), replace=False)
    return {k: float(silhouette_score(X[idx], KMeans(k, n_init=10, random_state=seed).fit_predict(X)[idx])) for k in ks}


def kmeans_clusters(rfm: pd.DataFrame, k: int, seed: int = 0) -> pd.Series:
    """Cluster labels named by their profile, ordered from most to least valuable."""
    labels = KMeans(k, n_init=10, random_state=seed).fit_predict(cluster_matrix(rfm))
    prof = rfm.assign(c=labels).groupby("c")[["recency_days", "frequency", "monetary"]].median()
    order = prof["monetary"].rank(ascending=False).astype(int)

    def name(c):
        p = prof.loc[c]
        value = (
            "High value"
            if p["monetary"] >= rfm["monetary"].quantile(0.7)
            else ("Mid value" if p["monetary"] >= rfm["monetary"].median() else "Low value")
        )
        state = "active" if p["recency_days"] <= 90 else ("cooling" if p["recency_days"] <= 240 else "dormant")
        return f"{order[c]}. {value}, {state}"

    return pd.Series([name(c) for c in labels], index=rfm.index, name="cluster")


# ---------- CLV ----------
def clv_features(rfm: pd.DataFrame) -> pd.DataFrame:
    X = rfm[
        [
            "recency_days",
            "frequency",
            "monetary",
            "aov",
            "tenure_days",
            "avg_gap_days",
            "discount_share",
            "items_per_order",
            "spend_90d",
            "orders_90d",
            "spend_180d",
            "orders_180d",
            "overdue_ratio",
            "recent_share",
        ]
    ].copy()
    for c in CHANNELS:
        X[f"ch_{c}"] = (rfm["channel"] == c).astype(int)
    for c in COUNTRIES:
        X[f"co_{c}"] = (rfm["country"] == c).astype(int)
    return X


def future_spend(orders: pd.DataFrame, index: pd.Index, cutoff: pd.Timestamp, window: int = WINDOW) -> pd.Series:
    fut = orders[(orders["date"] > cutoff) & (orders["date"] <= cutoff + pd.Timedelta(days=window))]
    return fut.groupby("customer_id")["amount"].sum().reindex(index, fill_value=0.0)


def _models():
    reg = HistGradientBoostingRegressor(
        loss="poisson", learning_rate=0.05, max_iter=300, min_samples_leaf=40, l2_regularization=1.0, random_state=0
    )
    clf = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=300, min_samples_leaf=40, l2_regularization=1.0, random_state=0
    )
    return reg, clf


def fit_clv(orders: pd.DataFrame, customers: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    """Train on features as of `cutoff` and spend in the WINDOW days after it."""
    rfm = rfm_table(orders, customers, cutoff)
    X, y = clv_features(rfm), future_spend(orders, rfm.index, cutoff)
    reg, clf = _models()
    return {"value": reg.fit(X, y), "buy_again": clf.fit(X, (y > 0).astype(int))}


def predict_clv(models: dict, rfm: pd.DataFrame) -> pd.DataFrame:
    X = clv_features(rfm)
    return pd.DataFrame(
        {"predicted_180d_value": models["value"].predict(X), "p_buy_again": models["buy_again"].predict_proba(X)[:, 1]},
        index=rfm.index,
    )


def capture(y_true: pd.Series, score: pd.Series, top: float = 0.2) -> float:
    """Share of actual future revenue held by the top `top` fraction of customers ranked by `score`."""
    n = int(len(score) * top)
    picked = score.sort_values(ascending=False).index[:n]
    return float(y_true.loc[picked].sum() / y_true.sum())


def evaluate_clv(orders: pd.DataFrame, customers: pd.DataFrame, end: pd.Timestamp) -> dict:
    """Out-of-time test: train on [.. end-2W] -> (end-2W, end-W], test on [.. end-W] -> (end-W, end]."""
    train_cut, test_cut = end - pd.Timedelta(days=2 * WINDOW), end - pd.Timedelta(days=WINDOW)
    models = fit_clv(orders, customers, train_cut)
    rfm = rfm_table(orders, customers, test_cut)
    y = future_spend(orders, rfm.index, test_cut)
    pred = predict_clv(models, rfm)
    baseline = rfm["spend_180d"]  # "they'll spend what they spent in the last 6 months"
    return {
        "train_cutoff": str(train_cut.date()),
        "test_cutoff": str(test_cut.date()),
        "customers_scored": len(rfm),
        "actual_revenue": float(y.sum()),
        "mae_model": float(mean_absolute_error(y, pred["predicted_180d_value"])),
        "mae_baseline": float(mean_absolute_error(y, baseline)),
        "top20_capture_model": capture(y, pred["predicted_180d_value"]),
        "top20_capture_baseline": capture(y, baseline + rfm["monetary"] * 1e-6),  # tiny tie-break by lifetime spend
        "auc_buy_again_model": float(roc_auc_score(y > 0, pred["p_buy_again"])),
        "auc_buy_again_recency": float(roc_auc_score(y > 0, -rfm["recency_days"])),
        "total_predicted": float(pred["predicted_180d_value"].sum()),
    }


def save(models: dict, metrics: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, directory / "models.joblib")
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2))


def load(directory: Path) -> tuple[dict, dict]:
    return joblib.load(directory / "models.joblib"), json.loads((directory / "metrics.json").read_text())
