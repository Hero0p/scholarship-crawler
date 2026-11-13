"""Page relevance scoring (F3), soft-404 detection (F2) and link scoring for the focused crawl."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from atlas import config
from atlas.extract.content import Document
from atlas.extract.fields import AMOUNT_RE, APPLY_RE, find_dates, name_ok


def _term_re(terms: list[str]) -> re.Pattern:
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)) + r")(?![a-z])", re.I)


_cache: dict[str, re.Pattern] = {}


def rx(kind: str) -> re.Pattern:
    if kind not in _cache:
        _cache[kind] = _term_re(config.keywords()[kind])
    return _cache[kind]


def deny_url_res() -> list[re.Pattern]:
    if "deny_url" not in _cache:
        _cache["deny_url"] = [re.compile(p, re.I) for p in config.keywords()["deny_url_patterns"]]  # type: ignore
    return _cache["deny_url"]  # type: ignore


def url_denied(url: str) -> bool:
    return any(p.search(url) for p in deny_url_res())


# ----------------------------------------------------------------------------- links

def link_score(anchor: str, url: str, in_scholarship_context: bool = False) -> int:
    path = urlsplit(url).path.replace("-", " ").replace("_", " ").replace("/", " ")
    anchor = anchor or ""
    s = 0
    if rx("strong_terms").search(anchor):
        s += 3
    if rx("strong_terms").search(path):
        s += 2
    if rx("weak_terms").search(anchor) or rx("weak_terms").search(path):
        s += 1
    if in_scholarship_context:
        s += 1
    if url.lower().split("?")[0].endswith(".pdf") and re.search(
            r"scholarship|fellowship|guideline|notification|advertisement|scheme", anchor + " " + path, re.I):
        s += 1
    if rx("deny_terms").search(anchor) or rx("deny_terms").search(path):
        s -= 5
    return s


# ----------------------------------------------------------------------------- soft 404

SOFT404_TITLE = re.compile(r"\b(404|not found|page not found|error|does not exist|cannot be displayed|"
                           r"page unavailable|no longer available|access denied|forbidden)\b", re.I)
SOFT404_URL = re.compile(r"/(error|errorpage|404|notfound|page-not-found)(?:[/.?]|$)|[?&](code|error)=404", re.I)


def is_soft_404(doc: Document, requested_url: str, final_url: str) -> tuple[bool, str]:
    if SOFT404_URL.search(final_url) and not SOFT404_URL.search(requested_url):
        return True, f"redirected to error URL {final_url}"
    head = " ".join([doc.title] + doc.h1[:1])
    if head and SOFT404_TITLE.search(head) and len(doc.text) < 4000:
        return True, f"error title: {head[:80]}"
    req_path = urlsplit(requested_url).path.strip("/")
    fin_path = urlsplit(final_url).path.strip("/")
    if req_path and len(req_path) > 3 and req_path.lower() != fin_path.lower() and \
            fin_path.lower() in ("", "index.html", "index.php", "home", "default.aspx", "index.aspx"):
        return True, "deep link redirected to homepage"
    if len(doc.text) < 120 and doc.kind == "HTML":
        return True, "almost empty page"
    return False, ""


# ----------------------------------------------------------------------------- relevance

@dataclass
class PageVerdict:
    kind: str           # DETAIL | LISTING | IRRELEVANT | SOFT_404
    score: float
    reasons: list[str]
    sections: list[str]


def classify_page(doc: Document, sections: dict, requested_url: str, final_url: str) -> PageVerdict:
    soft, why = is_soft_404(doc, requested_url, final_url)
    if soft:
        return PageVerdict("SOFT_404", -10, [why], [])
    reasons = []
    score = 0.0
    head = " ".join([doc.title, doc.og_title] + doc.h1[:2])
    if rx("strong_terms").search(head):
        score += 4
        reasons.append("strong term in title/h1")
    core = [s for s in sections if s in ("eligibility", "benefits", "apply", "documents", "dates", "selection")]
    score += 2 * min(len(core), 4)
    if core:
        reasons.append(f"sections: {', '.join(core)}")
    if AMOUNT_RE.search(doc.text):
        score += 2
        reasons.append("amount present")
    if find_dates(doc.text):
        score += 1
    if any(APPLY_RE.search(ln.text or "") for ln in doc.links):
        score += 1
    if rx("deny_terms").search(doc.title or "") or rx("deny_terms").search(urlsplit(final_url).path):
        score -= 4
        reasons.append("deny term in title/URL")
    # listing heuristics: many scholarship-like links in content
    sch_links = [ln for ln in doc.links if ln.in_content and rx("strong_terms").search(ln.text or "")]
    has_name = any(name_ok(c) for c in doc.h1[:3]) or any(
        name_ok(seg.strip()) for seg in re.split(r"[|\-–:]", doc.og_title or doc.title or ""))
    if score >= 8 and len(core) >= 2 and has_name:
        return PageVerdict("DETAIL", score, reasons, core)
    if len(sch_links) >= 3:
        reasons.append(f"{len(sch_links)} scholarship links")
        return PageVerdict("LISTING", score, reasons, core)
    return PageVerdict("IRRELEVANT", score, reasons, core)
