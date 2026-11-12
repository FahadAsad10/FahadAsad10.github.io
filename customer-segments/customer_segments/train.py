"""Evaluate, fit and save: python -m customer_segments.train"""

import time
from pathlib import Path

import pandas as pd

from customer_segments.data import END, generate
from customer_segments.model import (
    WINDOW,
    choose_k,
    cluster_matrix,
    evaluate_clv,
    fit_clv,
    rfm_table,
    save,
    score_rfm,
    segment_summary,
)


def build_metrics(customers, orders) -> dict:
    rfm = rfm_table(orders, customers, END)
    seg = segment_summary(score_rfm(rfm))
    sil = choose_k(cluster_matrix(rfm))
    top = rfm["monetary"].sort_values(ascending=False)
    return {
        "customers": len(rfm),
        "orders": len(orders),
        "revenue": float(orders["amount"].sum()),
        "top20_revenue_share": float(top.head(len(top) // 5).sum() / top.sum()),
        "segments": seg.set_index("segment")[["customers", "share_of_revenue"]].to_dict("index"),
        "silhouette": {str(k): v for k, v in sil.items()},
        "best_k": int(max(sil, key=sil.get)),
        "clv": evaluate_clv(orders, customers, END),
    }


def main() -> None:
    t0 = time.time()
    customers, orders = generate()
    m = build_metrics(customers, orders)
    print(f"{m['customers']:,} customers, {m['orders']:,} orders, revenue EUR {m['revenue'] / 1e6:.2f}M")
    print(f"Top 20% of customers = {m['top20_revenue_share']:.0%} of revenue")
    for name, s in m["segments"].items():
        print(f"  {name:<20} {s['customers']:>5} customers  {s['share_of_revenue']:>6.1%} of revenue")
    print("Silhouette by k: " + ", ".join(f"{k}={v:.3f}" for k, v in m["silhouette"].items()) + f" -> k={m['best_k']}")
    c = m["clv"]
    print(f"CLV out-of-time test ({c['test_cutoff']} + {WINDOW} days, {c['customers_scored']:,} customers):")
    print(f"  MAE           model EUR {c['mae_model']:.1f} vs last-6-months EUR {c['mae_baseline']:.1f}")
    print(
        f"  Top-20% catch model {c['top20_capture_model']:.1%} "
        f"vs last-6-months {c['top20_capture_baseline']:.1%} of future revenue"
    )
    print(f"  Buy-again AUC model {c['auc_buy_again_model']:.3f} vs recency {c['auc_buy_again_recency']:.3f}")
    print(f"  Predicted total EUR {c['total_predicted']:,.0f} vs actual EUR {c['actual_revenue']:,.0f}")
    save(fit_clv(orders, customers, END - pd.Timedelta(days=WINDOW)), m, Path("models"))
    print(f"Saved models/ in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
