"""Turn reviewed invoices into tables, a formatted Excel workbook and CSV files."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .schema import Invoice
from .validate import Issue, check, find_duplicates, status


@dataclass
class Reviewed:
    filename: str
    invoice: Invoice
    issues: list[Issue] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return any(i.field == "duplicate" for i in self.issues)

    @property
    def status(self) -> str:
        return status(self.issues)


def review(items: list[tuple[str, Invoice]], today: date | None = None) -> list[Reviewed]:
    """Run every check, including duplicates across the whole batch."""
    out = [Reviewed(name, inv, check(inv, today)) for name, inv in items]
    for i, j in find_duplicates([r.invoice for r in out]).items():
        out[i].issues.append(Issue("error", "duplicate", f"Looks like a duplicate of {out[j].filename}"))
    return out


def invoices_table(rows: list[Reviewed]) -> pd.DataFrame:
    records = []
    for r in rows:
        inv = r.invoice
        net = inv.subtotal if inv.subtotal is not None else sum(li.amount for li in inv.line_items)
        records.append(
            {
                "File": r.filename,
                "Status": r.status,
                "Type": inv.document_type.replace("_", " "),
                "Vendor": inv.vendor_name,
                "VAT number": inv.vendor_tax_id,
                "Invoice no.": inv.invoice_number,
                "Date": inv.invoice_date,
                "Due": inv.due_date,
                "Currency": inv.currency,
                "Net": round(net, 2),
                "VAT": round(inv.vat_total or 0.0, 2),
                "Total": round(inv.total, 2),
                "Lines": len(inv.line_items),
                "Issues": "; ".join(f"{i.field}: {i.message}" for i in r.issues),
            }
        )
    return pd.DataFrame(records)


def line_items_table(rows: list[Reviewed]) -> pd.DataFrame:
    """One row per line with the invoice header repeated: the flat shape accounting tools import."""
    records = []
    for r in rows:
        inv = r.invoice
        for n, li in enumerate(inv.line_items, 1):
            vat = li.amount * li.vat_rate / 100 if li.vat_rate is not None else None
            records.append(
                {
                    "File": r.filename,
                    "Vendor": inv.vendor_name,
                    "Invoice no.": inv.invoice_number,
                    "Date": inv.invoice_date,
                    "Currency": inv.currency,
                    "Line": n,
                    "Description": li.description,
                    "Qty": li.quantity,
                    "Unit price": li.unit_price,
                    "VAT %": li.vat_rate,
                    "Net": round(li.amount, 2),
                    "VAT": None if vat is None else round(vat, 2),
                    "Gross": None if vat is None else round(li.amount + vat, 2),
                }
            )
    return pd.DataFrame(records)


def vat_summary(rows: list[Reviewed]) -> pd.DataFrame:
    """Net and tax per currency and rate: the numbers a VAT return needs. Duplicates are left out."""
    lines = line_items_table([r for r in rows if not r.is_duplicate])
    if lines.empty:
        return pd.DataFrame(columns=["Currency", "VAT %", "Net", "VAT", "Gross", "Documents"])
    lines["VAT %"] = lines["VAT %"].fillna(-1)
    out = (
        lines.groupby(["Currency", "VAT %"], as_index=False)
        .agg(Net=("Net", "sum"), VAT=("VAT", "sum"), Gross=("Gross", "sum"), Documents=("File", "nunique"))
        .round(2)
    )
    out["VAT %"] = out["VAT %"].astype(object).where(out["VAT %"] >= 0, "not shown")
    return out


def to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel opens accents correctly


def to_excel(rows: list[Reviewed]) -> bytes:
    """A formatted workbook: Invoices, Line items and VAT summary sheets with filters and frozen headers."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sheets = {
        "Invoices": invoices_table(rows),
        "Line items": line_items_table(rows),
        "VAT summary": vat_summary(rows),
    }
    money = {"Net", "VAT", "Total", "Gross", "Unit price"}
    fills = {
        "Ready": PatternFill("solid", fgColor="D9F2E3"),
        "Check": PatternFill("solid", fgColor="FFF1CC"),
        "Needs review": PatternFill("solid", fgColor="FADBD8"),
    }
    header_fill = PatternFill("solid", fgColor="1F2430")

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = "A2"
            ws.page_setup.orientation = "landscape"  # prints one sheet wide
            ws.sheet_properties.pageSetUpPr.fitToPage = True
            ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
            if len(df):
                ws.auto_filter.ref = ws.dimensions
            for col_idx, col in enumerate(df.columns, 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = header_fill
                cell.alignment = Alignment(vertical="center")
                values = [str(v) for v in df[col].tolist() if v is not None]
                width = min(60, max([len(str(col))] + [len(v) for v in values]) + 2)
                ws.column_dimensions[get_column_letter(col_idx)].width = width
                if col in money:
                    for row in range(2, len(df) + 2):
                        ws.cell(row=row, column=col_idx).number_format = "#,##0.00"
                if col == "Status":
                    for row in range(2, len(df) + 2):
                        c = ws.cell(row=row, column=col_idx)
                        if c.value in fills:
                            c.fill = fills[c.value]
    return buf.getvalue()
