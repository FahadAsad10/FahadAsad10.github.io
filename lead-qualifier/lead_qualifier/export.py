"""Tables for the dashboard and exports for a CRM or an outbox."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from .score import TIER_ICON, Scored

ACTION_LABEL = {
    "book_call": "Book a call",
    "send_pricing": "Send pricing",
    "ask_questions": "Ask questions",
    "nurture": "Nurture",
    "forward_to_account_manager": "Forward to account manager",
    "polite_decline": "Politely decline",
    "ignore": "Ignore",
}


def due_label(reply_by: datetime | None, now: datetime | None = None) -> str:
    if reply_by is None:
        return ""
    now = now or datetime.now(UTC)
    if reply_by.tzinfo is None:
        reply_by = reply_by.replace(tzinfo=UTC)
    minutes = int((reply_by - now).total_seconds() // 60)
    span = abs(minutes)
    text = (
        f"{span} min" if span < 60 else f"{span // 60} h {span % 60:02d} min" if span < 24 * 60 else f"{span // 1440} d"
    )
    return f"Overdue {text}" if minutes < 0 else f"Reply in {text}"


def queue_table(rows: list[Scored], now: datetime | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "": TIER_ICON[s.tier],
                "Tier": s.tier,
                "Score": s.score,
                "Lead": s.lead.name or s.lead.email or s.lead.id,
                "Company": s.lead.company,
                "Wants": s.read.matched_service or "",
                "Deal value": round(s.deal_value),
                "Reply": due_label(s.reply_by, now),
                "Next step": ACTION_LABEL[s.read.next_action],
                "Source": s.lead.source,
                "ID": s.lead.id,
            }
            for s in rows
        ]
    )


def crm_table(rows: list[Scored]) -> pd.DataFrame:
    """One row per lead with HubSpot-style column names, ready for a CRM import."""
    return pd.DataFrame(
        [
            {
                "Lead ID": s.lead.id,
                "Full Name": s.lead.name,
                "Email": s.lead.email,
                "Company Name": s.lead.company,
                "Job Title": s.lead.role,
                "Website URL": s.facts.website or "",
                "Country": s.facts.country or "",
                "Industry": s.read.industry or "",
                "Company Size": s.read.company_size,
                "Lead Source": s.lead.source,
                "Lead Status": s.tier,
                "Lead Score": s.score,
                "Lead Type": s.read.lead_type,
                "Service Interest": s.read.matched_service or "",
                "Budget": s.read.budget_amount if s.read.budget_amount is not None else "",
                "Budget Currency": s.read.budget_currency or "",
                "Deal Value": round(s.deal_value),
                "Timeline": s.read.timeline,
                "Authority": s.read.authority,
                "Summary": s.read.summary,
                "Pain Points": "; ".join(s.read.pain_points),
                "Red Flags": "; ".join(s.read.red_flags + s.facts.flags),
                "Next Step": ACTION_LABEL[s.read.next_action],
                "Reply Due (UTC)": s.reply_by.strftime("%Y-%m-%d %H:%M") if s.reply_by else "",
                "Duplicate Of": s.facts.duplicate_of or "",
                "Received": s.lead.received_at,
            }
            for s in rows
        ]
    )


def to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel opens accents correctly


def replies_text(rows: list[Scored], replies: dict[str, str] | None = None) -> str:
    """All drafted replies in one file, edited versions first, ready to paste into an inbox."""
    replies = replies or {}
    parts = []
    for s in rows:
        body = replies.get(s.lead.id, s.read.reply_body)
        if not body or s.tier == "Duplicate":
            continue
        parts.append(
            f"To: {s.lead.name} <{s.lead.email}>\nSubject: {s.read.reply_subject or ''}\n"
            f"[{s.tier}, score {s.score}]\n\n{body.strip()}\n"
        )
    return ("\n" + "-" * 60 + "\n\n").join(parts)
