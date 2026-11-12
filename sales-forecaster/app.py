"""Sales Forecaster dashboard: forecast demand, test a promotion, and plan what to reorder."""

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from sales_forecaster.data import CATEGORIES, STORES, generate
from sales_forecaster.model import (
    HORIZON,
    KEYS,
    backtest,
    fit,
    load,
    predict,
    reorder_plan,
    sample_on_hand,
    save,
)
from sales_forecaster.model import future_frame as make_future

MODEL_DIR = Path("models")
ACCENT = "#4fd1c5"
PALETTE = ["#4fd1c5", "#8b7cff", "#f6ad55", "#fc8181", "#63b3ed"]

st.set_page_config(page_title="Sales Forecaster", page_icon="📈", layout="wide")


@st.cache_data
def get_data() -> pd.DataFrame:
    return generate()


@st.cache_resource
def get_models():
    if not (MODEL_DIR / "models.joblib").exists():
        with st.spinner("Backtesting and training the models for the first time…"):
            data = get_data()
            metrics, _ = backtest(data)
            base = metrics["wape"]["Same weekday, last 4 weeks"]
            metrics["error_reduction_vs_naive"] = 1 - metrics["wape"]["Gradient boosting (this model)"] / base
            models = fit(data)
            models["widen"] = metrics["band_widen"]
            save(models, metrics, MODEL_DIR)
    return load(MODEL_DIR)


@st.cache_data
def baseline_forecast() -> pd.DataFrame:
    models, _ = get_models()
    data = get_data()
    return predict(models, data, make_future(data))


def style(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=70, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#c9c9d6"),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right", title_text=""),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#24242f")
    fig.update_yaxes(gridcolor="#24242f")
    return fig


def band_chart(hist: pd.DataFrame, fc: pd.DataFrame, value: str, title: str, height: int = 360) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=hist["date"],
        y=hist[value],
        name="Actual",
        line=dict(color="#9a9aab", width=1.4),
    )
    fig.add_scatter(x=fc["date"], y=fc["hi"], line=dict(width=0), showlegend=False, hoverinfo="skip")
    fig.add_scatter(
        x=fc["date"],
        y=fc["lo"],
        fill="tonexty",
        fillcolor="rgba(79,209,197,.18)",
        line=dict(width=0),
        name="80% range",
    )
    fig.add_scatter(x=fc["date"], y=fc["mid"], name="Forecast", line=dict(color=ACCENT, width=2.4))
    fig.update_layout(title=title)
    return style(fig, height)


models, metrics = get_models()
data = get_data()
forecast = baseline_forecast()
last = data["date"].max()

st.title("📈 Sales Forecaster")
st.caption(
    f"Forecasts daily demand for {len(STORES)} stores × {len(CATEGORIES)} categories for the next {HORIZON} days, "
    f"then turns the forecast into a reorder plan. Weekly forecast error **{metrics['weekly_wape']['model']:.1%}** "
    f"vs **{metrics['weekly_wape']['naive']:.1%}** for the usual rule of thumb."
)

overview, drill, accuracy, reorder = st.tabs(["Overview", "Forecast & promo what-if", "Accuracy", "Reorder plan"])

with overview:
    last_year = data[
        (data["date"] > last - pd.Timedelta(days=365)) & (data["date"] <= last - pd.Timedelta(days=365 - HORIZON))
    ]
    fc_rev, ly_rev = forecast["revenue"].sum(), last_year["revenue"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        f"Revenue forecast, next {HORIZON} days",
        f"€{fc_rev / 1e6:.2f}M",
        f"{fc_rev / ly_rev - 1:+.1%} vs same period last year",
    )
    c2.metric(
        "Units forecast",
        f"{forecast['p50'].sum():,.0f}",
        f"{forecast['p50'].sum() / last_year['units'].sum() - 1:+.1%} vs same period last year",
    )
    c3.metric(
        "Weekly forecast error (WAPE)",
        f"{metrics['weekly_wape']['model']:.1%}",
        f"{(metrics['weekly_wape']['model'] - metrics['weekly_wape']['naive']) * 100:+.1f} pts vs rule of thumb",
        delta_color="inverse",
    )
    c4.metric(
        "Forecast error cut vs rule of thumb",
        f"{1 - metrics['weekly_wape']['model'] / metrics['weekly_wape']['naive']:.0%}",
        f"{metrics['folds']}-fold backtest",
        delta_color="off",
    )

    hist = data[data["date"] > last - pd.Timedelta(days=180)].groupby("date", as_index=False)["revenue"].sum()
    price = forecast["revenue"] / forecast["p50"]
    total = (
        forecast.assign(
            lo=forecast["p10"] * price,
            hi=forecast["p90"] * price,
            mid=forecast["revenue"],
        )
        .groupby("date", as_index=False)[["lo", "mid", "hi"]]
        .sum()
    )
    st.plotly_chart(
        band_chart(hist, total, "revenue", "Daily revenue, all stores (EUR)"),
        use_container_width=True,
    )

    left, right = st.columns(2)
    by_store = forecast.groupby("store", as_index=False)["revenue"].sum().sort_values("revenue")
    left.plotly_chart(
        style(
            px.bar(
                by_store,
                x="revenue",
                y="store",
                orientation="h",
                title=f"Forecast revenue by store, next {HORIZON} days",
                color_discrete_sequence=[ACCENT],
            )
        ),
        use_container_width=True,
    )
    yoy = (
        forecast.groupby("category")["revenue"]
        .sum()
        .rename("forecast")
        .to_frame()
        .join(last_year.groupby("category")["revenue"].sum().rename("last_year"))
        .assign(growth=lambda d: d["forecast"] / d["last_year"] - 1)
        .reset_index()
    )
    fig = px.bar(
        yoy,
        x="category",
        y="growth",
        title="Growth vs same period last year",
        color_discrete_sequence=[PALETTE[1]],
    )
    fig.update_yaxes(tickformat=".0%")
    right.plotly_chart(style(fig), use_container_width=True)

with drill:
    a, b = st.columns(2)
    store = a.selectbox("Store", list(STORES))
    cat = b.selectbox("Category", list(CATEGORIES))
    days = pd.date_range(last + pd.Timedelta(days=1), periods=HORIZON)
    st.markdown("**Promotion what-if.** Pick the days you plan to run a 15%-off promotion, and the forecast updates.")
    promo_range = st.slider(
        "Promotion window",
        min_value=days[0].date(),
        max_value=days[-1].date(),
        value=(days[6].date(), days[12].date()),
        format="DD MMM",
    )
    run_promo = st.toggle("Run this promotion", value=False)
    sel = forecast[(forecast["store"] == store) & (forecast["category"] == cat)]
    if run_promo:
        promo_days = pd.date_range(*promo_range)
        sel_new = predict(models, data, make_future(data, {(store, cat): list(promo_days)}))
        sel_new = sel_new[(sel_new["store"] == store) & (sel_new["category"] == cat)]
        d_units, d_rev = (
            sel_new["p50"].sum() - sel["p50"].sum(),
            sel_new["revenue"].sum() - sel["revenue"].sum(),
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Extra units", f"{d_units:+,.0f}", f"{d_units / sel['p50'].sum():+.1%}")
        m2.metric(
            "Revenue impact (after discount)",
            f"€{d_rev:+,.0f}",
            f"{d_rev / sel['revenue'].sum():+.1%}",
        )
        m3.metric("Promotion days", len(promo_days))
        sel = sel_new
    hist = data[(data["store"] == store) & (data["category"] == cat) & (data["date"] > last - pd.Timedelta(days=120))]
    st.plotly_chart(
        band_chart(
            hist,
            sel.rename(columns={"p10": "lo", "p50": "mid", "p90": "hi"}),
            "units",
            f"{cat} at {store}: daily units",
        ),
        use_container_width=True,
    )
    st.caption("The shaded range holds the actual value about 80% of the time (calibrated on the backtest).")

with accuracy:
    st.markdown(
        f"Rolling-origin backtest: the model is refit at each of **{metrics['folds']} cutoffs** and forecasts the next "
        f"**{HORIZON} days** it has never seen, exactly as it would be used in practice."
    )
    w = pd.DataFrame({"Method": list(metrics["wape"]), "Daily WAPE": list(metrics["wape"].values())})
    fig = px.bar(
        w,
        x="Daily WAPE",
        y="Method",
        orientation="h",
        title="Daily error by method (lower is better)",
        color="Method",
        color_discrete_sequence=PALETTE,
    )
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(style(fig, 280).update_layout(showlegend=False), use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Weekly WAPE, model", f"{metrics['weekly_wape']['model']:.1%}")
    c2.metric("Weekly WAPE, rule of thumb", f"{metrics['weekly_wape']['naive']:.1%}")
    c3.metric(
        "Forecast bias",
        f"{metrics['bias']:+.1%}",
    )
    c3.caption("Near zero: no systematic over- or under-ordering.")
    bc = (
        pd.DataFrame(metrics["by_category"])
        .T.reset_index(names="category")
        .melt("category", var_name="method", value_name="WAPE")
    )
    bc["method"] = bc["method"].map({"model": "Gradient boosting", "naive": "Same weekday, last 4 weeks"})
    fig = px.bar(
        bc,
        x="category",
        y="WAPE",
        color="method",
        barmode="group",
        title="Daily error by category",
        color_discrete_sequence=PALETTE,
    )
    fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(style(fig), use_container_width=True)
    st.caption(
        "WAPE = total absolute error ÷ total units sold. Weekly WAPE compares weekly totals per store and category, "
        "the level orders are placed at."
    )

with reorder:
    st.markdown(
        "Enter current stock (or upload a CSV with `store, category, on_hand`) and get an order list that covers "
        "demand until the next delivery with a 90% service level."
    )
    a, b = st.columns(2)
    lead = a.number_input("Supplier lead time (days)", 1, 14, 7)
    review = b.number_input("Days until the next order after this one", 1, 14, 7)
    with st.expander("Current stock levels (edit, or upload a CSV)"):
        upload = st.file_uploader("Stock levels CSV", type="csv")
        on_hand = pd.read_csv(upload) if upload else sample_on_hand(data)
        on_hand = st.data_editor(
            on_hand[KEYS + ["on_hand"]],
            hide_index=True,
            use_container_width=True,
            disabled=KEYS,
            key="stock",
        )
    plan = reorder_plan(forecast, on_hand, int(lead), int(review))
    m1, m2, m3 = st.columns(3)
    m1.metric("Items to reorder", f"{(plan['order_units'] > 0).sum()} of {len(plan)}")
    m2.metric("Order value (at cost)", f"€{plan['order_value_eur'].sum():,.0f}")
    m3.metric("High stockout risk", int((plan["stockout_risk"] == "High").sum()))
    st.dataframe(
        plan.assign(_r=plan["stockout_risk"].map({"High": 0, "Medium": 1, "Low": 2}))
        .sort_values(["_r", "order_value_eur"], ascending=[True, False])
        .drop(columns="_r"),
        hide_index=True,
        use_container_width=True,
        column_config={
            "store": "Store",
            "category": "Category",
            "on_hand": "On hand",
            "days_of_cover_now": st.column_config.NumberColumn("Days of cover now", format="%.1f"),
            "expected_demand": st.column_config.NumberColumn("Expected demand", format="%d"),
            "safety_stock": st.column_config.NumberColumn("Safety stock", format="%d"),
            "order_units": "Order units",
            "stockout_risk": "Stockout risk",
            "order_value_eur": st.column_config.NumberColumn("Order value €", format="€%d"),
        },
    )
    st.download_button(
        "Download order list (CSV)",
        plan.to_csv(index=False),
        "reorder_plan.csv",
        "text/csv",
    )
