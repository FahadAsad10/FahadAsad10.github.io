"""Lead Qualifier: inbound leads in, a prioritised queue with scores, reasons and first replies out."""

import os
import sys
from dataclasses import fields
from datetime import UTC, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from lead_qualifier.enrich import enrich
from lead_qualifier.export import ACTION_LABEL, crm_table, due_label, queue_table, replies_text, to_csv
from lead_qualifier.heuristic import read_lead
from lead_qualifier.intake import leads_from_csv, leads_from_text
from lead_qualifier.qualify import MODEL
from lead_qualifier.samples import PROFILE, sample_csv, sample_leads
from lead_qualifier.schema import BusinessProfile, Lead, LeadRead, Service
from lead_qualifier.score import TIER_ICON, TIERS, Rules, score_all

IN_BROWSER = sys.platform == "emscripten"  # the stlite demo on GitHub Pages
ACCENT = "#f5b971"
TIER_COLOR = {
    "Hot": "#ff7a59",
    "Warm": "#f5b971",
    "Nurture": "#4fd1c5",
    "Route": "#7f9cf5",
    "Duplicate": "#6b6b7b",
    "Disqualified": "#3a3a48",
}
SIGNAL_LABEL = {
    "stated_fits": "Budget fits",
    "stated_below": "Budget too low",
    "implied": "Budget implied",
    "none": "No budget given",
    "now": "Start within a month",
    "this_quarter": "This quarter",
    "later": "Later",
    "unknown": "Unknown",
    "decision_maker": "Decision maker",
    "influencer": "Influencer",
}

st.set_page_config(page_title="Lead Qualifier", page_icon="🎯", layout="wide")
st.markdown(
    """
<style>
[data-testid="stMetric"] {background: linear-gradient(160deg, #17171f 0%, #111118 100%);
  border: 1px solid #262633; border-radius: 14px; padding: 14px 16px;}
[data-testid="stMetricValue"] {font-size: 1.7rem;}
.chip {display:inline-block; padding:2px 10px; margin:0 6px 6px 0; border-radius:999px; font-size:0.82rem;
  background:#1c1c26; border:1px solid #2c2c3a; color:#d9d9e3;}
.chip.good {border-color:#2f6f5c; color:#7ee2c2;} .chip.bad {border-color:#7a3b34; color:#ff9a85;}
.msg {background:#13131a; border:1px solid #262633; border-left:3px solid #f5b971; border-radius:10px;
  padding:12px 14px; white-space:pre-wrap; font-size:0.93rem; color:#e4e4ec;}
.reason {display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #1f1f29;
  font-size:0.9rem;}
.reason b.pos {color:#7ee2c2;} .reason b.neg {color:#ff9a85;}
</style>
""",
    unsafe_allow_html=True,
)


def load_samples() -> None:
    items = sample_leads()
    st.session_state.leads = [lead for lead, _ in items]
    st.session_state.reads = {lead.id: read for lead, read in items}
    st.session_state.engine = {lead.id: "sample" for lead, _ in items}
    st.session_state.errors = {}
    st.session_state.replies = {}
    st.session_state.source = "samples"


def add_leads(new: list[Lead], reads: dict[str, LeadRead], engine: str) -> None:
    taken = {lead.id for lead in st.session_state.leads}
    for lead in new:
        if lead.id in taken:  # keep ids unique when adding a second batch
            n = len(taken) + 1
            while f"N{n:03d}" in taken:
                n += 1
            old = lead.id
            lead = lead.model_copy(update={"id": f"N{n:03d}"})
            if old in reads:
                reads[lead.id] = reads.pop(old)
        taken.add(lead.id)
        if lead.id in reads:
            st.session_state.leads.append(lead)
            st.session_state.reads[lead.id] = reads[lead.id]
            st.session_state.engine[lead.id] = engine


if "leads" not in st.session_state:
    load_samples()
if "profile" not in st.session_state:
    st.session_state.profile = PROFILE
if "rules" not in st.session_state:
    st.session_state.rules = Rules()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Leads")
    c1, c2 = st.columns(2)
    if c1.button("Load samples", use_container_width=True):
        load_samples()
    if c2.button("Clear all", use_container_width=True):
        st.session_state.leads, st.session_state.reads, st.session_state.engine = [], {}, {}
        st.session_state.replies, st.session_state.errors = {}, {}

    st.subheader("Add leads")
    if IN_BROWSER:
        st.info(
            "This live demo runs in your browser, so it can't call Claude. The 20 sample leads show Claude-style "
            "readings, and anything you paste is read by the **offline keyword reader** instead. To qualify real "
            "leads with Claude, [run the app](https://github.com/FahadAsad10/lead-qualifier#run-it) with an "
            "Anthropic API key."
        )
        how = "Paste text"
    else:
        how = st.radio("From", ["Upload CSV", "Paste text"], horizontal=True, label_visibility="collapsed")
    new_leads: list[Lead] = []
    if how == "Upload CSV":
        up = st.file_uploader("CSV export from a form tool or CRM", type=["csv"])
        st.download_button("Example CSV layout", sample_csv(), "example-leads.csv", "text/csv")
        if up is not None:
            try:
                new_leads = leads_from_csv(up.getvalue())
                st.caption(f"{len(new_leads)} leads found.")
            except ValueError as exc:
                st.error(str(exc))
    else:
        pasted = st.text_area(
            "Paste one or more leads (an email, a form submission, a job post). Separate leads with a line of ---",
            height=140,
            placeholder="Hi, I run a 40-person cleaning company and we schedule everything in Excel. "
            "Could you build something better? Budget around €5k, ideally before March.\n— Sara, sara@brightclean.eu",
        )
        if pasted.strip():
            new_leads = leads_from_text(pasted)

    if IN_BROWSER:
        use_claude, key = False, ""
    else:
        key = st.text_input(
            "Anthropic API key",
            type="password",
            placeholder="uses ANTHROPIC_API_KEY if set",
            help="Only kept for this session. Leads are sent to the Claude API and nothing is stored.",
        )
        has_key = bool(key or os.environ.get("ANTHROPIC_API_KEY"))
        use_claude = st.toggle("Read with Claude", value=has_key, disabled=not has_key)
        if not has_key:
            st.caption("No API key: leads are read by the offline keyword reader.")
    if st.button("Qualify leads", type="primary", disabled=not new_leads, use_container_width=True):
        profile: BusinessProfile = st.session_state.profile
        if use_claude:
            from lead_qualifier.qualify import make_client, qualify_many

            with st.spinner(f"Reading {len(new_leads)} lead(s) with {MODEL}…"):
                results = qualify_many(new_leads, profile, client=make_client(key or None))
            add_leads([lead for lead, r, _ in results], {lead.id: r for lead, r, _ in results if r}, "claude")
            st.session_state.errors.update({lead.id: e for lead, _, e in results if e})
        else:
            add_leads(new_leads, {lead.id: read_lead(lead, profile) for lead in new_leads}, "keywords")
        st.session_state.source = "mixed"

    with st.expander("Your business"):
        p: BusinessProfile = st.session_state.profile
        name = st.text_input("Business name", p.name)
        offer = st.text_area("What you sell", p.offer, height=90)
        services = st.data_editor(
            pd.DataFrame([s.model_dump() for s in p.services]).rename(
                columns={"name": "Service", "typical_value": f"Typical deal ({p.currency})"}
            ),
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
        )
        min_budget = st.number_input(f"Minimum budget ({p.currency})", 0, 1_000_000, int(p.min_budget), step=500)
        icp = st.text_area("Ideal customer", p.ideal_customer, height=90)
        nofit = st.text_area("Not a fit", p.not_a_fit, height=70)
        svc = [
            Service(name=str(r.iloc[0]), typical_value=float(r.iloc[1] or 0))
            for _, r in services.iterrows()
            if str(r.iloc[0]).strip() and str(r.iloc[0]) != "nan"
        ]
        st.session_state.profile = p.model_copy(
            update={
                "name": name,
                "offer": offer,
                "services": svc,
                "min_budget": min_budget,
                "ideal_customer": icp,
                "not_a_fit": nofit,
            }  # fmt: skip
        )
        st.caption("Claude reads new leads against this profile. Deal values update the pipeline right away.")

    with st.expander("Scoring rules"):
        r: Rules = st.session_state.rules
        st.caption("Points per signal. Change them and the whole queue re-scores instantly.")
        labels = {
            "service_match": "Wants a service we sell",
            "budget_fits": "Budget stated and fits",
            "budget_implied": "Budget implied",
            "timeline_now": "Start within a month",
            "timeline_quarter": "Start this quarter",
            "decision_maker": "Decision maker",
            "influencer": "Influencer",
            "size_in_icp": "Company size in sweet spot",
            "intent_high": "Asks to start / for a quote",
            "free_email": "Company lead on free email",
            "red_flag": "Each red flag",
        }
        changes = {}
        for f in fields(Rules):
            if f.name in labels:
                lo, hi = (-30, 0) if getattr(r, f.name) < 0 else (0, 40)
                changes[f.name] = st.slider(labels[f.name], lo, hi, getattr(r, f.name), key=f"rule_{f.name}")
        changes["hot_at"] = st.slider("Hot from score", 30, 100, r.hot_at)
        changes["warm_at"] = st.slider("Warm from score", 10, changes["hot_at"], min(r.warm_at, changes["hot_at"]))
        st.session_state.rules = Rules(**{**{f.name: getattr(r, f.name) for f in fields(Rules)}, **changes})
        if st.button("Reset to defaults", use_container_width=True):
            for f in fields(Rules):
                st.session_state.pop(f"rule_{f.name}", None)
            st.session_state.rules = Rules()
            st.rerun()

    st.divider()
    st.caption(
        f"Reading: Claude (`{MODEL}`) with structured outputs fills in the signals and drafts the reply. "
        "Scoring, tiers, deal values, duplicates and reply deadlines are plain Python, so every point has a "
        "visible reason."
    )

profile: BusinessProfile = st.session_state.profile
rules: Rules = st.session_state.rules
leads: list[Lead] = st.session_state.leads
facts = enrich(leads)
rows = score_all([(lead, st.session_state.reads[lead.id]) for lead in leads], facts, profile, rules)
now = datetime.now(UTC)

# ---------------------------------------------------------------- header
st.title("🎯 Lead Qualifier")
st.caption(
    "Every inbound lead read, scored and answered in seconds. Claude reads what each lead actually says and drafts "
    "a first reply; transparent rules turn that into a score, a priority and a reply deadline, so the best leads "
    "get a fast answer and the junk never reaches your inbox."
)
if st.session_state.source == "samples":
    st.caption(
        f"Showing 20 fictional leads for **{profile.name}**, a small automation studio. Their readings are "
        "pre-written in Claude's output format, so no API key is needed."
    )
for lid, err in st.session_state.errors.items():
    st.error(f"**{lid}**: {err}")
if not rows:
    st.info("No leads yet. Paste or upload some in the sidebar, or load the samples.")
    st.stop()

tiers = pd.Series([s.tier for s in rows]).value_counts()
urgent = [s for s in rows if s.tier in ("Hot", "Warm") and s.reply_by and (s.reply_by - now).total_seconds() < 3600]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Leads", len(rows))
c2.metric("🔥 Hot", int(tiers.get("Hot", 0)))
c3.metric("Reply within the hour", len(urgent), help="Hot and warm leads that are overdue or due in under an hour")
c4.metric(
    "Weighted pipeline",
    f"{profile.currency} {sum(s.expected_value for s in rows):,.0f}",
    help="Deal value × win rate per tier (Hot 35%, Warm 15%, Nurture 4%)",
)
c5.metric("Junk filtered out", int(tiers.get("Disqualified", 0) + tiers.get("Duplicate", 0)))

queue_tab, pipeline_tab, export_tab = st.tabs(["Inbox queue", "Pipeline", "Export"])

# ---------------------------------------------------------------- queue
with queue_tab:
    show = st.pills("Show", TIERS, selection_mode="multi", default=["Hot", "Warm", "Nurture", "Route"])
    visible = [s for s in rows if s.tier in (show or TIERS)]
    table = queue_table(visible, now)
    event = st.dataframe(
        table.drop(columns=["ID"]),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        height=min(36 * len(table) + 40, 620),
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Deal value": st.column_config.NumberColumn(f"Deal ({profile.currency})", format="localized"),
            "": st.column_config.TextColumn("", width="small"),
        },
    )
    picked = event.selection.rows[0] if event and event.selection.rows else 0
    if not visible:
        st.stop()
    s = visible[min(picked, len(visible) - 1)]
    lead, read, fx = s.lead, s.read, s.facts
    st.caption("Select a row to open that lead.")

    st.subheader(f"{TIER_ICON[s.tier]} {lead.name or lead.email or lead.id} · {lead.company or 'no company'}")
    engine = st.session_state.engine.get(lead.id)
    if engine == "keywords":
        st.warning("Read by the offline keyword reader, not Claude. Treat the signals and reply as a rough draft.")
    left, mid, right = st.columns([1.1, 1.1, 1])
    with left:
        st.markdown("**What they wrote**")
        st.markdown(f"<div class='msg'>{lead.message}</div>", unsafe_allow_html=True)
        st.markdown("")
        info = {
            "Email": lead.email,
            "Role": lead.role,
            "Source": lead.source,
            "Website": fx.website,
            "Country": fx.country,
            "Received": pd.Timestamp(lead.received_at).strftime("%d %b, %H:%M UTC") if lead.received_at else "",
        }
        st.markdown("  \n".join(f"**{k}:** {v}" for k, v in info.items() if v))
        if fx.free_email:
            st.caption("Free webmail address, so no company website to look up.")
        if fx.duplicate_of:
            st.info(f"Same person or company as **{fx.duplicate_of}**, which arrived first. Reply there instead.")
    with mid:
        st.markdown("**What Claude read**" if engine != "keywords" else "**What the keyword reader found**")
        st.markdown(f"_{read.summary}_")
        if read.need:
            st.markdown(f"**Need:** {read.need}")
        good = {"stated_fits", "now", "this_quarter", "decision_maker"}
        bad = {"stated_below", "later", "none"}
        chips = [(read.lead_type.replace("_", " ").capitalize(), "")]
        chips += [
            (SIGNAL_LABEL.get(x, x), "good" if x in good else "bad" if x in bad else "")
            for x in (read.budget_signal, read.timeline, read.authority)
        ]
        chips += [
            (f"Size: {read.company_size}", ""),
            (f"Intent: {read.intent}", "good" if read.intent == "high" else ""),
        ]
        if read.industry:
            chips.append((read.industry, ""))
        html = "".join(f"<span class='chip {kind}'>{label}</span>" for label, kind in chips)
        st.markdown(html, unsafe_allow_html=True)
        if read.budget_amount:
            st.markdown(f"**Budget:** {read.budget_currency or ''} {read.budget_amount:,.0f}")
        if read.pain_points:
            st.markdown("**Pain points**  \n" + "  \n".join(f"• {p}" for p in read.pain_points))
        flags = read.red_flags + [f for f in fx.flags if "earlier lead" not in f]
        if flags:
            st.markdown("**Red flags**  \n" + "  \n".join(f"⚠️ {f}" for f in flags))
        if read.questions_to_ask:
            st.markdown("**Ask on the first call**  \n" + "  \n".join(f"→ {q}" for q in read.questions_to_ask))
    with right:
        st.markdown(f"**Score {s.score} · {s.tier}**")
        if s.reasons:
            st.markdown(
                "".join(
                    f"<div class='reason'><span>{why}</span><b class='{'pos' if p > 0 else 'neg'}'>{p:+d}</b></div>"
                    for why, p in s.reasons
                ),
                unsafe_allow_html=True,
            )
        if s.tier in ("Disqualified", "Duplicate", "Route"):
            st.caption(
                {
                    "Disqualified": "Not a buyer (spam, a vendor, a job seeker or a student), so it's kept out of "
                    "the sales queue whatever the points say.",
                    "Duplicate": "Duplicates score 0 so the same person isn't chased twice.",
                    "Route": "Existing customers and partners go to a person, not the sales queue.",
                }[s.tier]
            )
        if read.budget_signal == "stated_below" and s.tier == "Nurture":
            st.caption("Capped at Nurture: the stated budget is under the minimum.")
        if s.deal_value:
            st.markdown(
                f"**Deal value:** {profile.currency} {s.deal_value:,.0f}  \n"
                f"**Weighted:** {profile.currency} {s.expected_value:,.0f}"
            )
        if s.reply_by:
            label = due_label(s.reply_by, now)
            (st.error if label.startswith("Overdue") else st.success)(label)
        st.markdown(f"**Next step:** {ACTION_LABEL[read.next_action]}")

    st.markdown("**First reply**")
    if read.reply_body and s.tier != "Duplicate":
        st.caption(f"Subject: {read.reply_subject}")
        edited = st.text_area(
            "Edit before sending",
            st.session_state.replies.get(lead.id, read.reply_body),
            height=230,
            key=f"reply_{lead.id}",
            label_visibility="collapsed",
        )
        if edited != read.reply_body:
            st.session_state.replies[lead.id] = edited
        if lead.email:
            from urllib.parse import quote

            href = f"mailto:{lead.email}?subject={quote(read.reply_subject or '')}&body={quote(edited)}"
            st.link_button("Open in email", href)
    else:
        st.caption("No reply: spam and duplicates are not answered.")

# ---------------------------------------------------------------- pipeline
with pipeline_tab:
    df = pd.DataFrame(
        [
            {
                "Tier": s.tier,
                "Source": s.lead.source,
                "Service": s.read.matched_service or "Unclear",
                "Score": s.score,
                "Deal": s.deal_value,
                "Weighted": s.expected_value,
            }
            for s in rows
        ]
    )
    layout = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#d9d9e3",
        margin=dict(l=10, r=10, t=40, b=10),
        legend_title_text="",
    )
    a, b = st.columns(2)
    with a:
        counts = df["Tier"].value_counts().reindex(TIERS).fillna(0).reset_index()
        counts.columns = ["Tier", "Leads"]
        fig = px.bar(
            counts, x="Leads", y="Tier", orientation="h", color="Tier", color_discrete_map=TIER_COLOR,
            title="Where this week's leads landed", text="Leads",
        )  # fmt: skip
        fig.update_layout(
            **layout, showlegend=False, yaxis=dict(categoryorder="array", categoryarray=TIERS[::-1], title="")
        )
        st.plotly_chart(fig, use_container_width=True)
    with b:
        live = df[df["Tier"].isin(["Hot", "Warm", "Nurture"]) & (df["Weighted"] > 0)]
        by_service = live.groupby(["Service", "Tier"], as_index=False)["Weighted"].sum()
        fig = px.bar(
            by_service, x="Weighted", y="Service", color="Tier", orientation="h", color_discrete_map=TIER_COLOR,
            title=f"Weighted pipeline by service ({profile.currency})", category_orders={"Tier": TIERS},
        )  # fmt: skip
        fig.update_layout(**layout, yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
    a, b = st.columns(2)
    with a:
        src = df.groupby(["Source", "Tier"], as_index=False).size()
        order = (
            df.assign(good=df["Tier"].isin(["Hot", "Warm"]))
            .groupby("Source")["good"]
            .mean()
            .sort_values(ascending=False)
        )
        fig = px.bar(
            src, x="size", y="Source", color="Tier", orientation="h", color_discrete_map=TIER_COLOR,
            category_orders={"Source": list(order.index), "Tier": TIERS},
            title="Lead quality by source (best first)",
        )  # fmt: skip
        fig.update_layout(**layout, xaxis_title="Leads", yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
        best = order.index[0]
        st.caption(f"**{best}** sends the highest share of hot and warm leads ({order.iloc[0]:.0%}).")
    with b:
        scored = df[~df["Tier"].isin(["Disqualified", "Duplicate", "Route"])]
        fig = go.Figure(
            go.Histogram(x=scored["Score"], xbins=dict(start=0, end=100, size=10), marker_color=ACCENT, opacity=0.85)
        )
        for x, name in ((rules.hot_at, "Hot"), (rules.warm_at, "Warm")):
            fig.add_vline(x=x, line_dash="dot", line_color=TIER_COLOR[name], annotation_text=name)
        fig.update_layout(**layout, title="Score distribution of real prospects", xaxis_title="Score",
                          yaxis_title="Leads", bargap=0.08, yaxis_dtick=1)  # fmt: skip
        st.plotly_chart(fig, use_container_width=True)
    junk = int(tiers.get("Disqualified", 0) + tiers.get("Duplicate", 0) + tiers.get("Route", 0))
    st.caption(
        f"{junk} of {len(rows)} leads never needed a salesperson's time: spam, vendors, job seekers, students, "
        "duplicates, and messages from existing customers or partners that go straight to the right person."
    )

# ---------------------------------------------------------------- export
with export_tab:
    crm = crm_table(rows)
    st.markdown("**For your CRM**: one row per lead with HubSpot-style column names (status, score, summary, "
                "budget, next step, reply deadline).")  # fmt: skip
    st.dataframe(crm, hide_index=True, use_container_width=True, height=280)
    stamp = now.strftime("%Y-%m-%d")
    c1, c2 = st.columns(2)
    c1.download_button(
        "⬇️ CRM import (CSV)", to_csv(crm), f"qualified-leads-{stamp}.csv", "text/csv", use_container_width=True
    )
    c2.download_button(
        "⬇️ All reply drafts (TXT)",
        replies_text(rows, st.session_state.replies),
        f"reply-drafts-{stamp}.txt",
        "text/plain",
        use_container_width=True,
    )
    st.caption("Edited replies are exported in their edited form. Nothing is sent automatically.")
