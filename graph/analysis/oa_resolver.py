from dataclasses import dataclass
from typing import Literal, Optional

import requests

from data_fetch import get_unpaywall_data, fetch_pdf_text

MIN_TEXT_CHARS = 200


@dataclass
class PaperResolution:
    text: Optional[str]
    source: Optional[str]  # "unpaywall" | "openalex" | "crossref" | None
    status: Literal["found", "not_found", "no_doi"]


def _accept(text: str | None) -> bool:
    return bool(text) and len(text) > MIN_TEXT_CHARS


def _try_unpaywall(doi: str, email: str) -> Optional[str]:
    if not email:
        return None
    try:
        ua_data = get_unpaywall_data(doi, email)
        if not ua_data:
            return None
        best = ua_data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf") or ua_data.get("doi_url")
        if not pdf_url:
            return None
        text = fetch_pdf_text(pdf_url)
        return text if _accept(text) else None
    except Exception:
        return None


def _try_openalex(doi: str) -> Optional[str]:
    try:
        r = requests.get(f"https://api.openalex.org/works/doi:{doi}", timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        pdf_url = (data.get("open_access") or {}).get("oa_url")
        if not pdf_url:
            return None
        text = fetch_pdf_text(pdf_url)
        return text if _accept(text) else None
    except Exception:
        return None


def _try_crossref(doi: str) -> Optional[str]:
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=10)
        if r.status_code != 200:
            return None
        message = r.json().get("message", {})
        pdf_url = None
        for link in message.get("link", []):
            if link.get("content-type") == "application/pdf":
                pdf_url = link.get("URL")
                break
        if not pdf_url:
            return None
        text = fetch_pdf_text(pdf_url)
        return text if _accept(text) else None
    except Exception:
        return None


def resolve_oa_pdf(doi: str, email: str, max_chars: int = 10000) -> PaperResolution:
    """Redundant OA-first paper resolution: Unpaywall -> OpenAlex -> Crossref.

    Never raises — every source is independently hardened so one source's
    failure (timeout, malformed response, connection error) can't block the
    others or crash the caller.
    """
    if doi in (None, "", "N/A"):
        return PaperResolution(text=None, source=None, status="no_doi")

    for source, fetcher in (
        ("unpaywall", lambda: _try_unpaywall(doi, email)),
        ("openalex", lambda: _try_openalex(doi)),
        ("crossref", lambda: _try_crossref(doi)),
    ):
        text = fetcher()
        if text:
            return PaperResolution(text=text[:max_chars], source=source, status="found")

    return PaperResolution(text=None, source=None, status="not_found")
