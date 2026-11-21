"""Deterministic checks on extracted invoices.

Claude reads the document; plain Python does the maths. Anything that doesn't add up is flagged for a human
instead of being silently "fixed", which is what a bookkeeper actually wants from automation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .schema import Invoice


@dataclass(frozen=True)
class Issue:
    level: str  # "error" (numbers don't reconcile) or "warning" (missing or unusual)
    field: str
    message: str


def _close(a: float, b: float, n_parts: int = 1) -> bool:
    """Equal within rounding: a cent per rounded part, or 0.1% of the amount for large totals."""
    return abs(a - b) <= max(0.011 * n_parts, 0.001 * max(abs(a), abs(b)))


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def check(inv: Invoice, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    items = inv.line_items
    cur = inv.currency

    for i, li in enumerate(items, 1):
        if li.quantity is not None and li.unit_price is not None:
            expected = li.quantity * li.unit_price
            if not _close(expected, li.amount):
                issues.append(
                    Issue(
                        "error",
                        f"line {i}",
                        f"{li.quantity:g} × {li.unit_price:,.2f} = {expected:,.2f}, but the line says {li.amount:,.2f}",
                    )
                )

    line_sum = sum(li.amount for li in items)
    if items and inv.subtotal is not None and not _close(line_sum, inv.subtotal, len(items)):
        issues.append(
            Issue(
                "error",
                "subtotal",
                f"Line items add up to {cur} {line_sum:,.2f}, but the subtotal is {cur} {inv.subtotal:,.2f}",
            )
        )

    net = inv.subtotal if inv.subtotal is not None else (line_sum if items else None)
    if net is not None and inv.vat_total is not None and not _close(net + inv.vat_total, inv.total):
        issues.append(
            Issue(
                "error",
                "total",
                f"Subtotal {net:,.2f} + tax {inv.vat_total:,.2f} = {net + inv.vat_total:,.2f}, "
                f"but the total is {inv.total:,.2f}",
            )
        )

    if items and all(li.vat_rate is not None for li in items) and inv.vat_total is not None:
        expected_vat = sum(li.amount * li.vat_rate / 100 for li in items)
        if not _close(expected_vat, inv.vat_total, len(items) + 1):
            issues.append(
                Issue(
                    "error",
                    "vat_total",
                    f"Tax at the printed rates comes to {expected_vat:,.2f}, "
                    f"but the document says {inv.vat_total:,.2f}",
                )
            )

    issued, due = _parse(inv.invoice_date), _parse(inv.due_date)
    if inv.invoice_date and issued is None:
        issues.append(Issue("warning", "invoice_date", f"Unreadable date {inv.invoice_date!r}"))
    if issued is None and not inv.invoice_date:
        issues.append(Issue("warning", "invoice_date", "No invoice date found"))
    if issued and issued > today:
        issues.append(Issue("warning", "invoice_date", f"Dated in the future ({issued})"))
    if issued and due and due < issued:
        issues.append(Issue("error", "due_date", f"Due date {due} is before the invoice date {issued}"))

    if inv.document_type in ("invoice", "credit_note"):
        if not inv.invoice_number:
            issues.append(Issue("warning", "invoice_number", "No invoice number found"))
        if (inv.vat_total or 0) != 0 and not inv.vendor_tax_id:
            issues.append(Issue("warning", "vendor_tax_id", "Tax is charged but no seller VAT number is shown"))

    if inv.notes:
        issues.append(Issue("warning", "notes", inv.notes))
    return issues


def _norm(s: str | None) -> str:
    s = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return re.sub(r"(ltd|limited|gmbh|inc|llc|plc|sas|srl|bv)$", "", s)


def find_duplicates(invoices: list[Invoice]) -> dict[int, int]:
    """Map index -> index of the earlier document it duplicates (same vendor + number, or vendor + date + total)."""
    seen: dict[tuple, int] = {}
    dupes: dict[int, int] = {}
    for i, inv in enumerate(invoices):
        vendor = _norm(inv.vendor_name)
        keys = [("total", vendor, inv.invoice_date, round(inv.total, 2))]
        if inv.invoice_number:
            keys.insert(0, ("number", vendor, _norm(inv.invoice_number)))
        for key in keys:
            if key in seen:
                dupes[i] = seen[key]
                break
        for key in keys:
            seen.setdefault(key, i)
    return dupes


def status(issues: list[Issue]) -> str:
    if any(i.level == "error" for i in issues):
        return "Needs review"
    if issues:
        return "Check"
    return "Ready"
