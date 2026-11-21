"""Invoice Extractor: drop in invoices and receipts, check what Claude read, export a clean spreadsheet."""

import io
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from invoice_extractor.export import (
    Reviewed,
    invoices_table,
    line_items_table,
    review,
    to_csv,
    to_excel,
    vat_summary,
)
from invoice_extractor.extract import MEDIA_TYPES, MODEL
from invoice_extractor.samples import SAMPLES
from invoice_extractor.schema import Invoice, LineItem

IN_BROWSER = sys.platform == "emscripten"  # the stlite demo on GitHub Pages
SAMPLE_DIR = Path("samples")
ACCENT = "#7f9cf5"
STATUS_ICON = {"Ready": "🟢", "Check": "🟡", "Needs review": "🔴"}

st.set_page_config(page_title="Invoice Extractor", page_icon="🧾", layout="wide")


def preview_image(name: str, data: bytes | None):
    """A picture of the document to check against: sample previews, uploaded images, or page 1 of a PDF."""
    p = SAMPLE_DIR / "previews" / f"{name}.jpg"
    if data is None:
        return str(p) if p.exists() else None
    if not name.lower().endswith(".pdf"):
        return data
    try:
        import pypdfium2 as pdfium

        img = pdfium.PdfDocument(data)[0].render(scale=1.3).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def load_samples() -> None:
    st.session_state.docs = {name: inv for name, inv in SAMPLES}
    st.session_state.files = {}
    st.session_state.errors = {}
    st.session_state.source = "samples"


if "docs" not in st.session_state:
    load_samples()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Documents")
    if st.button("Load the 6 sample documents", use_container_width=True):
        load_samples()
    if IN_BROWSER:
        st.info(
            "This live demo runs entirely in your browser, so it shows **pre-extracted sample documents**. "
            "To read your own invoices with Claude, run the app with an Anthropic API key "
            "([instructions](https://github.com/FahadAsad10/invoice-extractor#run-it))."
        )
    else:
        key = st.text_input(
            "Anthropic API key",
            type="password",
            value="",
            placeholder="uses ANTHROPIC_API_KEY if set",
            help="Only kept for this session. Documents are sent to the Claude API and nothing is stored.",
        )
        uploads = st.file_uploader(
            "Invoices or receipts (PDF, JPG, PNG, WEBP)",
            type=[s.lstrip(".") for s in MEDIA_TYPES],
            accept_multiple_files=True,
        )
        has_key = bool(key or os.environ.get("ANTHROPIC_API_KEY"))
        if st.button(
            "Extract with Claude", type="primary", disabled=not uploads or not has_key, use_container_width=True
        ):
            from invoice_extractor.extract import extract_many, make_client

            files = [(f.name, f.getvalue()) for f in uploads]
            with st.spinner(f"Reading {len(files)} document(s) with {MODEL}…"):
                results = extract_many(files, client=make_client(key or None))
            st.session_state.docs = {n: inv for n, inv, err in results if inv is not None}
            st.session_state.errors = {n: err for n, inv, err in results if err}
            st.session_state.files = dict(files)
            st.session_state.source = "upload"
        if uploads and not has_key:
            st.caption("Add an API key to extract. Or explore the samples first.")
    st.divider()
    st.caption(
        f"Extraction: Claude (`{MODEL}`) with structured outputs. Every total, tax rate and line is then "
        "re-checked in plain Python, and anything that doesn't add up is flagged instead of silently fixed."
    )

docs: dict[str, Invoice] = st.session_state.docs
rows: list[Reviewed] = review(list(docs.items()))

# ---------------------------------------------------------------- header
st.title("🧾 Invoice Extractor")
st.caption(
    "Turn a pile of supplier invoices and receipts into a clean spreadsheet. Claude reads each PDF or photo, "
    "the app checks the maths, flags duplicates and missing VAT numbers, and you export to Excel or CSV."
)
if st.session_state.source == "samples":
    st.caption("Showing 6 fictional sample documents with pre-extracted results (no API key needed).")
for name, err in st.session_state.errors.items():
    st.error(f"**{name}**: {err}")

if not rows:
    st.info("No documents yet. Upload some in the sidebar or load the samples.")
    st.stop()

counts = pd.Series([r.status for r in rows]).value_counts()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Documents", len(rows))
c2.metric("Ready to post", int(counts.get("Ready", 0)))
c3.metric("Flagged for review", int(counts.get("Needs review", 0) + counts.get("Check", 0)))
c4.metric("Line items captured", sum(len(r.invoice.line_items) for r in rows))

review_tab, spend_tab, export_tab = st.tabs(["Review", "Spend & VAT", "Export"])

# ---------------------------------------------------------------- review
with review_tab:
    table = invoices_table(rows)
    table.insert(0, "", table["Status"].map(STATUS_ICON))
    st.dataframe(
        table.drop(columns=["File", "Lines", "Type", "VAT number", "Due"]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Net": st.column_config.NumberColumn(format="%.2f"),
            "VAT": st.column_config.NumberColumn(format="%.2f"),
            "Total": st.column_config.NumberColumn(format="%.2f"),
            "Issues": st.column_config.TextColumn(width="large"),
        },
    )

    names = [r.filename for r in rows]
    pick = st.selectbox(
        "Open a document to check it against the original",
        names,
        format_func=lambda n: f"{STATUS_ICON[rows[names.index(n)].status]}  {n}",
    )
    r = rows[names.index(pick)]
    inv = r.invoice
    left, right = st.columns([5, 6], gap="large")
    with left:
        img = preview_image(pick, st.session_state.files.get(pick))
        if img is not None:
            st.image(img, use_container_width=True)
        else:
            st.caption("No preview available for this file.")
    with right:
        for issue in r.issues:
            (st.error if issue.level == "error" else st.warning)(f"**{issue.field}**: {issue.message}")
        if not r.issues:
            st.success("Everything adds up. Ready to post.")

        with st.form(f"edit-{pick}"):
            st.markdown("**Extracted fields** (edit anything that's wrong, then save)")
            a, b = st.columns(2)
            vendor = a.text_input("Vendor", inv.vendor_name)
            tax_id = b.text_input("Seller VAT number", inv.vendor_tax_id or "")
            number = a.text_input("Invoice no.", inv.invoice_number or "")
            currency = b.text_input("Currency", inv.currency)
            issued = a.text_input("Invoice date (YYYY-MM-DD)", inv.invoice_date or "")
            due = b.text_input("Due date (YYYY-MM-DD)", inv.due_date or "")
            a, b, c = st.columns(3)
            subtotal = a.number_input("Net", value=float(inv.subtotal or 0), format="%.2f")
            vat_total = b.number_input("VAT", value=float(inv.vat_total or 0), format="%.2f")
            total = c.number_input("Total", value=float(inv.total), format="%.2f")
            lines = st.data_editor(
                pd.DataFrame([li.model_dump() for li in inv.line_items]),
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "description": "Description",
                    "quantity": st.column_config.NumberColumn("Qty"),
                    "unit_price": st.column_config.NumberColumn("Unit price", format="%.2f"),
                    "vat_rate": st.column_config.NumberColumn("VAT %"),
                    "amount": st.column_config.NumberColumn("Net amount", format="%.2f"),
                },
            )
            if st.form_submit_button("Save corrections", type="primary"):
                clean = lines.dropna(subset=["description", "amount"]).astype(object)
                items = [
                    LineItem(**{k: (None if pd.isna(v) else v) for k, v in row.items()})
                    for row in clean.to_dict("records")
                ]
                docs[pick] = inv.model_copy(
                    update=dict(
                        vendor_name=vendor,
                        vendor_tax_id=tax_id or None,
                        invoice_number=number or None,
                        currency=currency.upper(),
                        invoice_date=issued or None,
                        due_date=due or None,
                        subtotal=subtotal,
                        vat_total=vat_total,
                        total=total,
                        line_items=items,
                        notes=None,  # a human has now checked the notes
                    )
                )
                st.rerun()

# ---------------------------------------------------------------- spend
with spend_tab:
    inv_df = invoices_table([r for r in rows if not r.is_duplicate])
    currencies = inv_df.groupby("Currency")["Total"].count().sort_values(ascending=False).index.tolist()
    cur = st.radio("Currency", currencies, horizontal=True) if len(currencies) > 1 else currencies[0]
    sub = inv_df[inv_df["Currency"] == cur]
    a, b = st.columns(2)
    with a:
        by_vendor = sub.groupby("Vendor", as_index=False)[["Net", "VAT"]].sum().sort_values("Net")
        fig = px.bar(
            by_vendor,
            x=["Net", "VAT"],
            y="Vendor",
            orientation="h",
            title=f"Spend by supplier ({cur})",
            color_discrete_sequence=[ACCENT, "#f6ad55"],
        )
        fig.update_layout(
            height=320,
            margin=dict(l=10, r=10, t=60, b=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#c9c9d6"),
            legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right", title_text=""),
            xaxis_title="",
            yaxis_title="",
        )
        fig.update_xaxes(gridcolor="#24242f")
        st.plotly_chart(fig, use_container_width=True)
    with b:
        st.markdown(f"**VAT by rate ({cur})**")
        vs = vat_summary(rows)
        money = {c: st.column_config.NumberColumn(format="%.2f") for c in ["Net", "VAT", "Gross"]}
        st.dataframe(
            vs[vs["Currency"] == cur].drop(columns="Currency"),
            hide_index=True,
            use_container_width=True,
            column_config=money,
        )
        st.caption("Reclaimable input tax per rate, ready for the VAT return. Duplicates are excluded.")
    st.markdown("**Due for payment**")
    due = sub[["Vendor", "Invoice no.", "Due", "Total"]].dropna(subset=["Due"]).sort_values("Due")
    st.dataframe(due, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- export
with export_tab:
    st.markdown(
        "The Excel workbook has three sheets: **Invoices** (one row per document, with status and issues), "
        "**Line items** (one row per line with the invoice header repeated, the shape accounting tools import) "
        "and **VAT summary**. Headers are frozen, filters are on, and statuses are colour-coded."
    )
    stamp = date.today().isoformat()
    a, b, c = st.columns(3)
    a.download_button(
        "⬇ Excel workbook (.xlsx)",
        to_excel(rows),
        f"invoices-{stamp}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )
    b.download_button(
        "⬇ Invoices (.csv)", to_csv(invoices_table(rows)), f"invoices-{stamp}.csv", "text/csv", use_container_width=True
    )
    c.download_button(
        "⬇ Line items (.csv)",
        to_csv(line_items_table(rows)),
        f"line-items-{stamp}.csv",
        "text/csv",
        use_container_width=True,
    )
    only_ready = st.toggle("Preview only documents that are ready to post", value=False)
    preview_rows = [r for r in rows if r.status == "Ready"] if only_ready else rows
    st.dataframe(line_items_table(preview_rows).drop(columns="File"), hide_index=True, use_container_width=True)
