"""Get leads in: a CSV export from a form tool or CRM, or text pasted from an email or chat."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime

import pandas as pd

from .schema import Lead

ALIASES = {
    "name": ["name", "full name", "fullname", "contact", "contact name", "your name"],
    "first": ["first name", "firstname", "first"],
    "last": ["last name", "lastname", "surname", "last"],
    "email": ["email", "e-mail", "email address", "work email", "mail"],
    "company": ["company", "company name", "organisation", "organization", "business", "account"],
    "role": ["role", "job title", "title", "position"],
    "source": ["source", "lead source", "channel", "utm_source", "form"],
    "country": ["country", "location", "region"],
    "message": ["message", "description", "comments", "comment", "notes", "inquiry", "enquiry", "details", "body",
                "how can we help", "project details", "what do you need"],
    "received_at": ["received_at", "date", "submitted", "submitted at", "created", "created at", "timestamp", "time"],
}  # fmt: skip


def _norm(col: str) -> str:
    return re.sub(r"[\s_?:]+", " ", str(col)).strip().lower()


def _find(columns: dict[str, str], key: str) -> str | None:
    for alias in ALIASES[key]:
        if alias in columns:
            return columns[alias]
    return None


def _cell(row, col: str | None) -> str:
    if col is None:
        return ""
    v = row[col]
    return "" if pd.isna(v) else str(v).strip()


def _timestamp(value: str) -> str:
    if not value:
        return datetime.now(UTC).isoformat(timespec="minutes")
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return datetime.now(UTC).isoformat(timespec="minutes")
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.isoformat(timespec="minutes")


def leads_from_csv(data: bytes | str) -> list[Lead]:
    """Map whatever columns the export has onto `Lead`. A message column is required."""
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    df = pd.read_csv(io.StringIO(text), dtype=str)
    columns = {_norm(c): c for c in df.columns}
    cols = {k: _find(columns, k) for k in ALIASES}
    if cols["message"] is None:
        raise ValueError("No message column found. Name it 'message', 'description', 'notes' or 'comments'.")
    leads = []
    for i, row in df.iterrows():
        message = _cell(row, cols["message"])
        if not message:
            continue
        name = _cell(row, cols["name"]) or f"{_cell(row, cols['first'])} {_cell(row, cols['last'])}".strip()
        leads.append(
            Lead(
                id=f"L{i + 1:03d}",
                name=name,
                email=_cell(row, cols["email"]),
                company=_cell(row, cols["company"]),
                role=_cell(row, cols["role"]),
                source=_cell(row, cols["source"]) or "CSV import",
                country=_cell(row, cols["country"]),
                message=message,
                received_at=_timestamp(_cell(row, cols["received_at"])),
            )
        )
    return leads


EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def leads_from_text(text: str, source: str = "Pasted") -> list[Lead]:
    """Pasted text. Separate several leads with a line containing only '---'."""
    blocks = [b.strip() for b in re.split(r"^\s*-{3,}\s*$", text, flags=re.MULTILINE) if b.strip()]
    now = datetime.now(UTC).isoformat(timespec="minutes")
    leads = []
    for i, block in enumerate(blocks, 1):
        email = EMAIL_IN_TEXT.search(block)
        leads.append(
            Lead(
                id=f"P{i:03d}",
                email=email.group(0) if email else "",
                source=source,
                message=block,
                received_at=now,
            )
        )
    return leads
