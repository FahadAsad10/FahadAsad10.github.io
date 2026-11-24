"""Turn Claude's reading of a lead into a score, a tier, a deal value and a reply deadline.

This part is plain arithmetic on purpose: every point has a reason you can read, and the weights are yours to
change. The AI reads; the business decides what matters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .enrich import Facts
from .schema import BusinessProfile, Lead, LeadRead

TIERS = ["Hot", "Warm", "Nurture", "Route", "Duplicate", "Disqualified"]
TIER_ICON = {
    "Hot": "🔥",
    "Warm": "🟠",
    "Nurture": "🌱",
    "Route": "↪️",
    "Duplicate": "🔁",
    "Disqualified": "⛔",
}
DISQUALIFY = {"vendor_pitch", "job_seeker", "student_or_research", "spam"}
ROUTE = {"existing_customer", "partner", "other"}
# Rough conversion into the business currency, for pipeline totals only.
FX_TO_EUR = {
    "EUR": 1.0,
    "GBP": 1.17,
    "USD": 0.92,
    "CHF": 1.05,
    "AED": 0.25,
    "PKR": 0.0033,
}


@dataclass
class Rules:
    """Points per signal. Defaults sum to 100 for a perfect lead."""

    service_match: int = 20
    budget_fits: int = 25
    budget_implied: int = 12
    budget_below: int = -10
    timeline_now: int = 20
    timeline_quarter: int = 12
    timeline_later: int = 4
    decision_maker: int = 15
    influencer: int = 7
    size_in_icp: int = 10  # small or mid
    size_enterprise: int = 5
    size_unknown: int = 3
    intent_high: int = 10
    intent_medium: int = 5
    free_email: int = -5
    red_flag: int = -5  # per flag, at most three
    hot_at: int = 70
    warm_at: int = 45
    win_rate: dict[str, float] = field(default_factory=lambda: {"Hot": 0.35, "Warm": 0.15, "Nurture": 0.04})
    reply_within_hours: dict[str, float] = field(
        default_factory=lambda: {"Hot": 1, "Warm": 4, "Nurture": 24, "Route": 24}
    )


@dataclass
class Scored:
    lead: Lead
    read: LeadRead
    facts: Facts
    score: int
    tier: str
    reasons: list[tuple[str, int]]
    deal_value: float  # in the business currency
    expected_value: float
    reply_by: datetime | None


def to_business_currency(amount: float, currency: str | None, profile: BusinessProfile) -> float:
    cur = (currency or profile.currency).upper()
    if cur == profile.currency.upper():
        return amount
    eur = amount * FX_TO_EUR.get(cur, 1.0)
    return eur / FX_TO_EUR.get(profile.currency.upper(), 1.0)


def points(read: LeadRead, facts: Facts, profile: BusinessProfile, rules: Rules) -> list[tuple[str, int]]:
    r: list[tuple[str, int]] = []
    services = {s.name for s in profile.services}
    if read.matched_service in services:
        r.append((f"Wants a service we sell: {read.matched_service}", rules.service_match))
    budget = {
        "stated_fits": ("Budget stated and fits", rules.budget_fits),
        "implied": ("Budget implied, no number", rules.budget_implied),
        "stated_below": ("Budget below our minimum", rules.budget_below),
    }.get(read.budget_signal)
    if budget:
        r.append(budget)
    timeline = {
        "now": ("Wants to start within a month", rules.timeline_now),
        "this_quarter": ("Wants to start this quarter", rules.timeline_quarter),
        "later": ("Timeline is later", rules.timeline_later),
    }.get(read.timeline)
    if timeline:
        r.append(timeline)
    authority = {
        "decision_maker": ("Talking to the decision maker", rules.decision_maker),
        "influencer": ("Influencer, not the final buyer", rules.influencer),
    }.get(read.authority)
    if authority:
        r.append(authority)
    size = {
        "small": ("Company size is in our sweet spot", rules.size_in_icp),
        "mid": ("Company size is in our sweet spot", rules.size_in_icp),
        "enterprise": (
            "Enterprise: bigger deal, slower process",
            rules.size_enterprise,
        ),
        "unknown": ("Company size unknown", rules.size_unknown),
    }.get(read.company_size)
    if size:
        r.append(size)
    intent = {
        "high": ("Asks to start or for a quote", rules.intent_high),
        "medium": ("Concrete project", rules.intent_medium),
    }
    if read.intent in intent:
        r.append(intent[read.intent])
    if facts.free_email and read.company_size not in ("solo", "unknown"):
        r.append(("Company lead using a free email address", rules.free_email))
    flags = (read.red_flags + facts.flags)[:3]
    for flag in flags:
        if "earlier lead" not in flag:
            r.append((f"Red flag: {flag}", rules.red_flag))
    return [(why, pts) for why, pts in r if pts != 0]


def tier_for(score: int, read: LeadRead, facts: Facts, rules: Rules) -> str:
    if facts.duplicate_of:
        return "Duplicate"
    if read.lead_type in DISQUALIFY or facts.disposable_email or facts.invalid_email:
        return "Disqualified"
    if read.lead_type in ROUTE:
        return "Route"
    if read.budget_signal == "stated_below":
        return "Nurture"  # a stated budget under the minimum is never chased as hot or warm, whatever else fits
    if score >= rules.hot_at:
        return "Hot"
    if score >= rules.warm_at:
        return "Warm"
    return "Nurture"


def deal_value(read: LeadRead, profile: BusinessProfile) -> float:
    if read.budget_amount and read.budget_signal in ("stated_fits", "stated_below"):
        return to_business_currency(read.budget_amount, read.budget_currency, profile)
    for s in profile.services:
        if s.name == read.matched_service:
            return s.typical_value
    return 0.0


def parse_time(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def score_lead(lead: Lead, read: LeadRead, facts: Facts, profile: BusinessProfile, rules: Rules) -> Scored:
    reasons = points(read, facts, profile, rules)
    score = max(0, min(100, sum(p for _, p in reasons)))
    tier = tier_for(score, read, facts, rules)
    if tier in ("Disqualified", "Duplicate"):
        score = 0
    value = deal_value(read, profile) if tier in ("Hot", "Warm", "Nurture") else 0.0
    received = parse_time(lead.received_at)
    hours = rules.reply_within_hours.get(tier)
    reply_by = received + timedelta(hours=hours) if received and hours is not None else None
    return Scored(
        lead=lead,
        read=read,
        facts=facts,
        score=score,
        tier=tier,
        reasons=reasons,
        deal_value=value,
        expected_value=value * rules.win_rate.get(tier, 0.0),
        reply_by=reply_by,
    )


def score_all(
    items: list[tuple[Lead, LeadRead]],
    facts: dict[str, Facts],
    profile: BusinessProfile,
    rules: Rules | None = None,
) -> list[Scored]:
    """Score every lead and sort the queue: tier first, then the earliest reply deadline, then score."""
    rules = rules or Rules()
    out = [score_lead(lead, read, facts[lead.id], profile, rules) for lead, read in items]
    far = datetime.max
    return sorted(
        out,
        key=lambda s: (
            TIERS.index(s.tier),
            (s.reply_by.replace(tzinfo=None) if s.reply_by else far),
            -s.score,
        ),
    )
