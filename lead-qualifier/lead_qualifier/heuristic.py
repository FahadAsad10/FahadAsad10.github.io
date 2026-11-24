"""A keyword-only reading of a lead, for when there is no API key (the browser demo, or a quick offline pass).

It fills the same `LeadRead` fields as Claude so the same scoring applies, but it only spots words. It cannot
tell sarcasm, context or a polite "not now", and its reply is a template. Claude's reading is the real one."""

from __future__ import annotations

import re

from .enrich import facts_for
from .schema import BusinessProfile, Lead, LeadRead
from .score import to_business_currency

SERVICE_WORDS = {
    "AI automation": ["automat", "manual", "copy paste", "copy-paste", "data entry", "invoice", "workflow",
                      "spreadsheet", "excel", "process", "extract", "zapier", "integrat"],
    "Dashboards & analytics": ["dashboard", "report", "kpi", "analytics", "power bi", "metrics", "insight"],
    "Website": ["website", "landing page", "web site", "shopify", "wordpress", "redesign"],
    "Chatbot & support assistant": ["chatbot", "chat bot", "assistant", "whatsapp", "faq", "booking", "support"],
}  # fmt: skip
TYPE_WORDS = {
    "spam": ["crypto", "casino", "bitcoin", "forex", "guaranteed returns", "ignore previous", "ignore all previous"],
    "vendor_pitch": ["backlink", "seo services", "we offer", "our agency can", "lead generation services", "outsourc"],
    "job_seeker": ["internship", "my cv", "my resume", "hiring", "job opening", "position at your"],
    "student_or_research": ["thesis", "dissertation", "survey for my", "university project", "research project"],
    "partner": ["white label", "white-label", "partnership", "subcontract", "refer clients"],
    "existing_customer": ["our existing", "you built", "you set up", "our account", "support ticket"],
}  # fmt: skip
NOW_WORDS = [
    "asap",
    "urgent",
    "this week",
    "next week",
    "immediately",
    "right away",
    "this month",
    "2 weeks",
    "two weeks",
]
QUARTER_WORDS = [
    "next month",
    "this quarter",
    "january",
    "february",
    "march",
    "q1",
    "within 3 months",
    "soon",
]
LATER_WORDS = [
    "next year",
    "later this year",
    "q3",
    "q4",
    "after the summer",
    "no rush",
    "eventually",
]
DM_ROLES = [
    "owner",
    "founder",
    "ceo",
    "coo",
    "cfo",
    "cto",
    "director",
    "head of",
    "partner",
    "president",
    "md",
]
INFLUENCER_ROLES = [
    "manager",
    "lead",
    "coordinator",
    "specialist",
    "analyst",
    "assistant",
]
MONEY = re.compile(
    r"(€|eur|£|gbp|\$|usd)\s?(\d[\d,.]*)\s?(k)?|(\d[\d,.]*)\s?(k)?\s?(€|eur|£|gbp|\$|usd)",
    re.IGNORECASE,
)
CUR = {"€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "$": "USD", "usd": "USD"}


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _budget(text: str) -> tuple[float | None, str | None]:
    best: tuple[float | None, str | None] = (None, None)
    for m in MONEY.finditer(text):
        sym, num, k = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(6), m.group(4), m.group(5))
        try:
            value = float(num.replace(",", "")) * (1000 if k else 1)
        except ValueError:
            continue
        if best[0] is None or value > best[0]:
            best = (value, CUR[sym.lower()])
    return best


def read_lead(lead: Lead, profile: BusinessProfile) -> LeadRead:
    if facts_for(lead).invalid_email:
        return LeadRead(
            lead_type="other", summary="Email address is not valid", budget_signal="none", timeline="unknown",
            authority="unknown", company_size="unknown", intent="low", next_action="ignore",
        )  # fmt: skip
    text = f"{lead.message} {lead.company}".lower()
    role = lead.role.lower()
    lead_type = next((t for t, words in TYPE_WORDS.items() if _has(text, words)), "prospect")
    service = next(
        (s.name for s in profile.services if _has(text, SERVICE_WORDS.get(s.name, [s.name.lower()]))),
        None,
    )
    amount, currency = _budget(lead.message)
    if amount is not None:
        fits = to_business_currency(amount, currency, profile) >= profile.min_budget
        budget_signal = "stated_fits" if fits else "stated_below"
    else:
        budget_signal = "implied" if _has(text, ["budget approved", "budget is", "we pay", "paying for"]) else "none"
    timeline = (
        "now" if _has(text, NOW_WORDS)
        else "this_quarter" if _has(text, QUARTER_WORDS)
        else "later" if _has(text, LATER_WORDS)
        else "unknown"
    )  # fmt: skip
    authority = (
        "decision_maker" if _has(role, DM_ROLES) or _has(text, ["my company", "my business", "i own", "i run"])
        else "influencer" if _has(role, INFLUENCER_ROLES)
        else "unknown"
    )  # fmt: skip
    asks = _has(
        text,
        ["quote", "proposal", "call", "price", "cost", "how much", "start", "hire"],
    )
    intent = "high" if asks and service else "medium" if service else "low"
    first = (lead.name.split() or ["there"])[0]
    spam = lead_type == "spam"
    if spam:
        subject, body, action = None, None, "ignore"
    elif lead_type == "prospect":
        topic = service.lower() if service else "your project"
        subject = f"Re: {topic}"
        body = (
            f"Hi {first},\n\nThanks for getting in touch about {topic}. To point you in the right direction, "
            "could you share roughly what you have in mind, your timeline and a budget range? Then we can set up "
            f"a short call.\n\nBest,\n{profile.name}"
        )
        action = "book_call" if intent == "high" else "ask_questions"
    else:
        subject = "Thanks for your message"
        body = (
            f"Hi {first},\n\nThanks for reaching out. We've passed your message to the right person.\n\n{profile.name}"
        )
        action = "forward_to_account_manager" if lead_type in ("existing_customer", "partner") else "polite_decline"
    return LeadRead(
        lead_type=lead_type,
        summary=f"Keyword reading: {lead_type.replace('_', ' ')}" + (f" about {service}" if service else ""),
        need=None,
        matched_service=service if lead_type == "prospect" else None,
        budget_signal=budget_signal,
        budget_amount=amount,
        budget_currency=currency,
        timeline=timeline,
        authority=authority,
        company_size="unknown",
        intent=intent,
        red_flags=["Contains instructions aimed at an AI screener"]
        if "ignore" in text and "instruction" in text
        else [],
        questions_to_ask=[] if spam else ["What does the process look like today?", "When do you want it live?"],
        next_action=action,
        reply_subject=subject,
        reply_body=body,
    )
