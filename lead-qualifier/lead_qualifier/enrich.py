"""Deterministic enrichment and checks on the raw lead: no AI, no network, just facts you can verify."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Lead

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com", "hotmail.it", "outlook.com",
    "live.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.de", "gmx.net",
    "web.de", "libero.it", "mail.com", "yandex.com", "zoho.com",
}  # fmt: skip
DISPOSABLE = {
    "mailinator.com",
    "10minutemail.com",
    "guerrillamail.com",
    "tempmail.com",
    "yopmail.com",
    "trashmail.com",
}
TLD_COUNTRY = {
    "uk": "United Kingdom", "de": "Germany", "it": "Italy", "fr": "France", "es": "Spain", "nl": "Netherlands",
    "be": "Belgium", "ie": "Ireland", "at": "Austria", "ch": "Switzerland", "se": "Sweden", "dk": "Denmark",
    "no": "Norway", "pl": "Poland", "pt": "Portugal", "pk": "Pakistan", "in": "India", "ae": "UAE",
    "au": "Australia", "ca": "Canada", "us": "United States",
}  # fmt: skip
EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


@dataclass
class Facts:
    domain: str | None = None
    website: str | None = None
    free_email: bool = False
    disposable_email: bool = False
    invalid_email: bool = False
    country: str | None = None
    links: int = 0
    shouting: bool = False
    duplicate_of: str | None = None
    flags: list[str] = field(default_factory=list)


def email_domain(email: str) -> str | None:
    m = EMAIL_RE.match(email.strip().lower())
    return m.group(1) if m else None


def facts_for(lead: Lead) -> Facts:
    f = Facts()
    if lead.email.strip():
        f.domain = email_domain(lead.email)
        if f.domain is None:
            f.invalid_email = True
            f.flags.append("Email address is not valid")
    if f.domain:
        f.free_email = f.domain in FREE_MAIL
        f.disposable_email = f.domain in DISPOSABLE
        if not (f.free_email or f.disposable_email):
            f.website = f"https://{f.domain}"
        if f.disposable_email:
            f.flags.append("Disposable email address")
        tld = f.domain.rsplit(".", 1)[-1]
        f.country = TLD_COUNTRY.get(tld)
    f.country = lead.country or f.country
    f.links = len(URL_RE.findall(lead.message))
    letters = [c for c in lead.message if c.isalpha()]
    f.shouting = len(letters) > 40 and sum(c.isupper() for c in letters) / len(letters) > 0.6
    if f.links >= 2:
        f.flags.append(f"{f.links} links in the message")
    if f.shouting:
        f.flags.append("Message is mostly capitals")
    return f


def enrich(leads: list[Lead]) -> dict[str, Facts]:
    """Facts per lead id, with duplicates marked: same email, or same company domain within the batch.
    The earliest submission is kept as the original."""
    out: dict[str, Facts] = {}
    seen_email: dict[str, str] = {}
    seen_domain: dict[str, str] = {}
    for lead in sorted(leads, key=lambda x: x.received_at or ""):
        f = facts_for(lead)
        email = lead.email.strip().lower()
        if email and email in seen_email:
            f.duplicate_of = seen_email[email]
        elif f.website and f.domain in seen_domain:
            f.duplicate_of = seen_domain[f.domain]
        if f.duplicate_of:
            f.flags.append("Same person or company as an earlier lead")
        if email:
            seen_email.setdefault(email, lead.id)
        if f.website and f.domain:
            seen_domain.setdefault(f.domain, lead.id)
        out[lead.id] = f
    return out
