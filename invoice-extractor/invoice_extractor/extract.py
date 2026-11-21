"""Send one invoice or receipt (PDF or photo) to Claude and get a validated `Invoice` back."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .schema import Invoice

MODEL = "claude-opus-5-5"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

SYSTEM = """You are a meticulous bookkeeping assistant. You read one supplier invoice, receipt or credit note \
and record exactly what is printed on it.

Rules:
- Copy numbers as printed. Never correct the document's arithmetic, even if it looks wrong: \
the totals are checked separately and a mismatch is something the bookkeeper needs to see.
- Amounts are plain numbers without currency symbols or thousands separators. Read European formats \
correctly (1.234,56 means 1234.56).
- Dates are YYYY-MM-DD. Resolve day/month order from the country, currency and other dates on the page.
- Use null for anything that is not on the document. Do not guess tax IDs, numbers or dates.
- A line's vat_rate is the rate that applies to that line, in percent.
- On a credit note, line amounts and totals are negative.
- If part of the document is unreadable, cut off or handwritten, say so in notes."""


class ExtractionError(RuntimeError):
    """Claude could not return a usable result for this document."""


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ValueError(f"Unsupported file type {suffix!r}. Use PDF, PNG, JPG, WEBP or GIF.")
    return MEDIA_TYPES[suffix]


def content_block(data: bytes, filename: str) -> dict:
    """A `document` block for PDFs, an `image` block for photos and scans."""
    media_type = media_type_for(filename)
    source = {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode()}
    return {"type": "document" if media_type == "application/pdf" else "image", "source": source}


def make_client(api_key: str | None = None):
    import anthropic  # imported lazily so sample mode runs without the SDK (e.g. in the browser demo)

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def extract(data: bytes, filename: str, client=None, model: str = MODEL) -> Invoice:
    """Extract one document. Raises ExtractionError if Claude declines or the output is cut off."""
    client = client or make_client()
    response = client.beta.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block(data, filename),
                    {"type": "text", "text": f"Extract this document ({filename})."},
                ],
            }
        ],
        output_format=Invoice,
        output_config={"effort": "medium"},
        # If a safety classifier declines the request, the API retries it on a suitable model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise ExtractionError(f"{filename}: Claude declined to process this document.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError(f"{filename}: the document is too long to extract in one pass.")
    if response.parsed_output is None:
        raise ExtractionError(f"{filename}: no structured result was returned.")
    return response.parsed_output


def extract_many(
    files: Iterable[tuple[str, bytes]], client=None, model: str = MODEL, workers: int = 4
) -> list[tuple[str, Invoice | None, str | None]]:
    """Extract several documents in parallel. Returns (filename, invoice or None, error or None) in input order."""
    client = client or make_client()
    files = list(files)

    def run(item: tuple[str, bytes]):
        name, data = item
        try:
            return name, extract(data, name, client=client, model=model), None
        except Exception as exc:  # one bad file must not stop the batch
            return name, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run, files))
