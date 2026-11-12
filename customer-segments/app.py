"""Customer Segments dashboard: who your customers are, what each group is worth, and what to do next."""

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from customer_segments.data import END, generate
from customer_segments.model import (
    PLAYBOOK,
    SEGMENT_ORDER,
    WINDOW,
    fit_clv,
    kmeans_clusters,
    load,
    predict_clv,
    rfm_table,
    save,
    score_rfm,
    segment_summary,
)

MODEL_DIR = Path("models")
ACCENT = "#f6ad55"
SEG_COLORS = dict(
    zip(
        SEGMENT_ORDER,
        ["#f6ad55", "#f6e05e", "#68d391", "#4fd1c5", "#63b3ed", "#fc8181", "#e53e3e", "#a0aec0", "#4a5568"],
        strict=True,
    )
)

st.set_page_config(page_title="Customer Segments", page_icon="🎯", layout="wide")


@st.cache_data
def get_data():
    return generate()


@st.cache_resource
def get_models():
    if not (MODEL_DIR / "models.joblib").exists():
        from customer_segments.train import build_metrics

        with st.spinner("Segmenting customers and training the value model for the first time…"):
            customers, orders = get_data()
            save(
                fit_clv(orders, customers, END - pd.Timedelta(days=WINDOW)), build_metrics(customers, orders), MODEL_DIR
            )
    return load(MODEL_DIR)


@st.cache_data
def get_scored() -> pd.DataFrame:
    customers, orders = get_data()
    models, metrics = get_models()
    rfm = rfm_table(orders, customers, END)
    scored = score_rfm(rfm).join(predict_clv(models, rfm))
    scored["cluster"] = kmeans_clusters(rfm, metrics["best_k"])
    # Usual spend over a WINDOW-day period from lifetime history (min. one year, so new customers aren't inflated)
    scored["usual_spend"] = scored["monetary"] / np.maximum(scored["tenure_days"], 365) * WINDOW
    return scored


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=70, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c9c9d6"),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right", title_text=""),
    )
    fig.update_xaxes(gridcolor="#24242f")
    fig.update_yaxes(gridcolor="#24242f")
    return fig


models, metrics = get_models()
customers, orders = get_data()
scored = get_scored()
summary = segment_summary(scored, "predicted_180d_value")
risk_segments = ["At risk", "Can't lose them"]
at_risk = scored[scored["segment"].isin(risk_segments)]

st.title("🎯 Customer Segments")
st.caption(
    f"Groups {len(scored):,} online-shop customers by how recently, how often and how much they buy, "
    f"predicts what each will spend in the next {WINDOW} days, and turns it into a marketing playbook."
)

overview, playbook, clusters, value = st.tabs(["Overview", "Segment playbook", "Clusters", "Customer value"])

with overview:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customers", f"{len(scored):,}", f"{len(orders):,} orders", delta_color="off")
    c2.metric("Revenue, 2 years", f"€{scored['monetary'].sum() / 1e6:.2f}M")
    c3.metric("Revenue from top 20% of customers", f"{metrics['top20_revenue_share']:.0%}")
    c4.metric(
        f"Usual {WINDOW}-day spend going quiet",
        f"€{at_risk['usual_spend'].sum() / 1e3:,.0f}k",
        f"{len(at_risk):,} customers going quiet",
        delta_color="off",
    )
    left, right = st.columns([1.1, 1])
    fig = px.treemap(
        summary,
        path=["segment"],
        values="customers",
        color="segment",
        color_discrete_map=SEG_COLORS,
        title="Segments by number of customers",
        custom_data=["share_of_revenue"],
    )
    fig.update_traces(texttemplate="<b>%{label}</b><br>%{value} customers<br>%{customdata[0]:.0%} of revenue")
    left.plotly_chart(style(fig, 420), use_container_width=True)
    comp = summary.melt("segment", ["share_of_customers", "share_of_revenue"], var_name="measure", value_name="share")
    comp["measure"] = comp["measure"].map({"share_of_customers": "Customers", "share_of_revenue": "Revenue"})
    fig = px.bar(
        comp,
        y="segment",
        x="share",
        color="measure",
        barmode="group",
        orientation="h",
        title="Share of customers vs share of revenue",
        color_discrete_sequence=["#63b3ed", ACCENT],
    )
    fig.update_xaxes(tickformat=".0%")
    fig.update_yaxes(categoryorder="array", categoryarray=SEGMENT_ORDER[::-1], title=None)
    right.plotly_chart(style(fig, 420), use_container_width=True)

    curve = scored["monetary"].sort_values(ascending=False).cumsum() / scored["monetary"].sum()
    pareto = pd.DataFrame({"customers": np.arange(1, len(curve) + 1) / len(curve), "revenue": curve.to_numpy()})
    fig = px.area(
        pareto,
        x="customers",
        y="revenue",
        title="Revenue concentration (Pareto curve)",
        color_discrete_sequence=[ACCENT],
    )
    fig.add_vline(x=0.2, line_dash="dot", line_color="#9a9aab")
    fig.update_xaxes(tickformat=".0%", title="Share of customers, best first")
    fig.update_yaxes(tickformat=".0%", title="Share of revenue")
    st.plotly_chart(style(fig, 300), use_container_width=True)

with playbook:
    st.markdown(
        "What each segment is, what it's worth, and what to do about it. "
        "Pick a segment to export its customer list for your email or ads tool."
    )
    summary_pct = summary.assign(share_of_revenue=summary["share_of_revenue"] * 100)
    table = summary_pct[
        [
            "segment",
            "customers",
            "share_of_revenue",
            "avg_recency_days",
            "avg_orders",
            "avg_order_value",
            "predicted_180d_value",
            "action",
            "channel",
        ]
    ]
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        column_config={
            "segment": "Segment",
            "customers": "Customers",
            "share_of_revenue": st.column_config.ProgressColumn(
                "Share of revenue", format="%.0f%%", min_value=0, max_value=100
            ),
            "avg_recency_days": st.column_config.NumberColumn("Days since last order", format="%.0f"),
            "avg_orders": st.column_config.NumberColumn("Orders", format="%.1f"),
            "avg_order_value": st.column_config.NumberColumn("Avg order", format="€%.0f"),
            "predicted_180d_value": st.column_config.NumberColumn(f"Next {WINDOW}d value", format="€%.0f"),
            "action": st.column_config.TextColumn("Recommended action", width="large"),
            "channel": "Channel",
        },
    )
    seg = st.selectbox("Segment", [s for s in SEGMENT_ORDER if s in set(scored["segment"])], index=5)
    who, action, channel = PLAYBOOK[seg]
    st.info(f"**{seg}**: {who}  \n**Do this:** {action}  \n**Channel:** {channel}")
    members = scored[scored["segment"] == seg].sort_values("predicted_180d_value", ascending=False)
    export = members.assign(p_buy_again=members["p_buy_again"] * 100).reset_index()[
        [
            "customer_id",
            "country",
            "channel",
            "recency_days",
            "frequency",
            "monetary",
            "predicted_180d_value",
            "p_buy_again",
        ]
    ]
    st.dataframe(
        export.head(200),
        hide_index=True,
        use_container_width=True,
        column_config={
            "monetary": st.column_config.NumberColumn("Lifetime spend", format="€%.0f"),
            "predicted_180d_value": st.column_config.NumberColumn(f"Next {WINDOW}d value", format="€%.0f"),
            "p_buy_again": st.column_config.ProgressColumn(
                "Chance to buy again", format="%.0f%%", min_value=0, max_value=100
            ),
        },
    )
    st.download_button(
        f"Download {seg} list ({len(export):,} customers, CSV)",
        export.to_csv(index=False),
        f"{seg.lower().replace(' ', '_')}.csv",
        "text/csv",
    )

with clusters:
    st.markdown(
        f"K-means on log-scaled recency, frequency and spend finds **{metrics['best_k']} natural groups** "
        "(best silhouette score). It's a check on the rule-based segments: do the data's own groups agree?"
    )
    left, right = st.columns([1, 2])
    sil = pd.DataFrame({"k": list(metrics["silhouette"]), "silhouette": list(metrics["silhouette"].values())})
    fig = px.bar(
        sil,
        x="k",
        y="silhouette",
        title="Silhouette score by k (higher is better)",
        color_discrete_sequence=["#63b3ed"],
    )
    left.plotly_chart(style(fig, 380), use_container_width=True)
    sample = scored.sample(min(2500, len(scored)), random_state=0).sort_values("cluster")
    fig = px.scatter(
        sample,
        x="recency_days",
        y="monetary",
        color="cluster",
        log_y=True,
        opacity=0.65,
        title="Customers by recency and lifetime spend",
        labels={"recency_days": "Days since last order", "monetary": "Lifetime spend (€, log)"},
        color_discrete_sequence=[ACCENT, "#68d391", "#63b3ed", "#fc8181", "#a0aec0", "#f6e05e", "#b794f4"],
    )
    right.plotly_chart(style(fig, 380), use_container_width=True)
    ct = pd.crosstab(scored["cluster"], scored["segment"]).reindex(
        columns=[s for s in SEGMENT_ORDER if s in set(scored["segment"])]
    )
    fig = px.imshow(
        ct,
        text_auto=True,
        aspect="auto",
        color_continuous_scale=["#15151d", ACCENT],
        title="Clusters vs RFM segments (customers)",
    )
    fig.update_xaxes(title=None)
    fig.update_yaxes(title=None)
    st.plotly_chart(style(fig, 340), use_container_width=True)
    prof = scored.groupby("cluster")[["recency_days", "frequency", "monetary", "aov"]].median().round(0)
    prof.insert(0, "customers", scored["cluster"].value_counts())
    st.dataframe(
        prof,
        use_container_width=True,
        column_config={
            "recency_days": "Median days since last order",
            "frequency": "Median orders",
            "monetary": st.column_config.NumberColumn("Median lifetime spend", format="€%.0f"),
            "aov": st.column_config.NumberColumn("Median order value", format="€%.0f"),
        },
    )

with value:
    c = metrics["clv"]
    st.markdown(
        f"Gradient boosting predicts each customer's spend over the next **{WINDOW} days** and their chance of "
        f"buying again. It was tested **out of time**: trained on behaviour up to {c['train_cutoff']}, then asked "
        f"to predict the {WINDOW} days after {c['test_cutoff']}, and compared with the usual shortcut of assuming "
        "customers repeat their last six months."
    )
    m1, m2, m3 = st.columns(3)
    m1.metric(
        "Error per customer (MAE)",
        f"€{c['mae_model']:.0f}",
        f"{'-' if c['mae_model'] < c['mae_baseline'] else '+'}€{abs(c['mae_model'] - c['mae_baseline']):.0f} "
        "vs last-6-months rule",
        delta_color="inverse",
    )
    m2.metric(
        "Future revenue held by predicted top 20%",
        f"{c['top20_capture_model']:.0%}",
        f"{(c['top20_capture_model'] - c['top20_capture_baseline']) * 100:+.1f} pts vs ranking by past spend",
    )
    m3.metric(
        "Buy-again AUC",
        f"{c['auc_buy_again_model']:.2f}",
        f"{c['auc_buy_again_model'] - c['auc_buy_again_recency']:+.3f} vs recency alone",
    )

    st.subheader("Win-back shortlist")
    st.caption("Valuable customers who are unlikely to come back on their own: the best use of a retention budget.")
    winback = scored[(scored["p_buy_again"] < 0.5) & (scored["recency_days"] > 60) & (scored["frequency"] >= 2)]
    winback = winback.sort_values("usual_spend", ascending=False).head(25).reset_index()
    winback["p_buy_again"] = winback["p_buy_again"] * 100
    st.dataframe(
        winback[
            [
                "customer_id",
                "segment",
                "country",
                "recency_days",
                "frequency",
                "monetary",
                "p_buy_again",
                "usual_spend",
            ]
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            "monetary": st.column_config.NumberColumn("Lifetime spend", format="€%.0f"),
            "p_buy_again": st.column_config.ProgressColumn(
                "Chance to buy again", format="%.0f%%", min_value=0, max_value=100
            ),
            "usual_spend": st.column_config.NumberColumn(f"Usual {WINDOW}d spend at stake", format="€%.0f"),
            "customer_id": "Customer",
            "segment": "Segment",
            "country": "Country",
            "recency_days": "Days since last order",
            "frequency": "Orders",
        },
    )
    st.subheader("Look up a customer")
    cid = st.number_input("Customer ID", 0, int(scored.index.max()), int(winback.loc[0, "customer_id"]))
    row = scored.loc[cid]
    a, b, d, e = st.columns(4)
    a.metric("Segment", row["segment"])
    b.metric(
        "Lifetime spend",
        f"€{row['monetary']:,.0f}",
        f"{int(row['frequency'])} orders",
        delta_color="off",
    )
    d.metric(f"Predicted next {WINDOW} days", f"€{row['predicted_180d_value']:,.0f}")
    e.metric("Chance to buy again", f"{row['p_buy_again']:.0%}")
    hist = orders[orders["customer_id"] == cid]
    fig = px.bar(hist, x="date", y="amount", title=f"Order history, customer {cid}", color_discrete_sequence=[ACCENT])
    st.plotly_chart(style(fig, 260), use_container_width=True)
