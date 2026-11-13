"""Card-list extraction: official listing pages that show many schemes, each as a 'card' with its
own name, dates and a link to the official guideline document (e.g. NSP 'All Scholarships').

Generic DOM rule (no per-site code): a card is the smallest container around a scholarship-name
element that also contains a dated closing label, and contains no other scholarship name.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import lxml.html

from atlas.classify.source_classifier import is_application_platform
from atlas.extract.content import DROP_TAGS, Document, _collect_blocks, _assemble, norm_ws
from atlas.extract.fields import (CLOSING_CTX, INDIA_NEG, INDIA_POS, NAME_FEMALE, Extracted, _levels_in,
                                  extract_categories, extract_dates, find_dates, name_ok)
from atlas.extract.record import RecordDraft, funding_type_of
from atlas.urls import registered_domain

NAME_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "a", "p", "div", "span", "td", "li", "label", "dt"}
DETAIL_LINK = re.compile(r"specification|guideline|scheme details|details|know more|read more|notification|"
                         r"brochure|information|scheme document|prospectus", re.I)
PROVIDER_PREFIX = re.compile(r"^([A-Z][A-Za-z&.]{1,15}(?:\s[A-Z][A-Za-z&.]{1,15}){0,3})\s+-\s+\S")


def _own_name(el) -> str | None:
    t = norm_ws(el.text_content())
    if not (8 <= len(t) <= 220) or not name_ok(t):
        return None
    for ch in el:
        if isinstance(ch.tag, str) and norm_ws(ch.text_content()) == t:
            return None          # a child carries the same text: use the innermost element
    return t


def extract_cards(raw: bytes, url: str, doc: Document, domestic: bool = True) -> list[tuple[RecordDraft, str | None]]:
    try:
        root = lxml.html.document_fromstring(raw)
    except Exception:  # noqa: BLE001
        return []
    for bad in root.xpath("//" + " | //".join(DROP_TAGS)):
        bad.drop_tree()
    body = root.find("body") if root.find("body") is not None else root
    names = []
    for el in body.iter():
        if isinstance(el.tag, str) and el.tag in NAME_TAGS:
            n = _own_name(el)
            if n:
                names.append((el, n))
    name_els = {id(el) for el, _ in names}
    out: list[tuple[RecordDraft, str | None]] = []
    seen_containers = set()
    seen_names: set[str] = set()
    cursor = 0
    for el, name in names:
        container = None
        anc = el
        for _ in range(5):
            anc = anc.getparent()
            if anc is None or anc.tag in ("body", "html"):
                break
            inner_names = sum(1 for d in anc.iter() if id(d) in name_els)
            if inner_names > 1:
                break
            txt = norm_ws(anc.text_content())
            if len(txt) > 1500:
                break
            if CLOSING_CTX.search(txt) and find_dates(txt):
                container = anc
                break
        if container is None or name in seen_names:
            continue
        ckey = container.getroottree().getpath(container)   # id() of lxml proxies can be reused
        if ckey in seen_containers:
            continue
        seen_containers.add(ckey)
        seen_names.add(name)
        # mini document of the card (same block algorithm as the page text)
        mini = Document(url=url, kind="HTML")
        mini.blocks = _collect_blocks(container)
        _assemble(mini, [])
        _, cb, opening = extract_dates(mini, {})
        if cb is None:
            continue
        india_ev = None
        if not domestic:
            # foreign listing: the card itself must show Indian students are eligible
            m_in = INDIA_POS.search(mini.text)
            if not m_in or INDIA_NEG.search(mini.text):
                continue
            india_ev = mini.blocks[[b.start <= m_in.start() < b.end for b in mini.blocks].index(True)].text
        # anchor everything on the page text (evidence must be a verbatim substring of doc.text)
        npos = doc.text.find(name, cursor)
        if npos < 0:
            npos = doc.text.find(name)
        if npos < 0:
            continue
        cursor = npos + len(name)
        d = RecordDraft()
        d.fields["name"] = Extracted(name, name, npos, npos + len(name), extractor="name:card", pass_="META")

        def relocate(f: Extracted | None) -> Extracted | None:
            if f is None:
                return None
            p = doc.text.find(f.evidence, npos)
            if p < 0 or p - npos > 2500:
                return None
            return Extracted(f.value, f.evidence, p, p + len(f.evidence), section="card", extractor=f.extractor,
                             pass_="SECTION", extra=f.extra)

        c = relocate(cb)
        if c is None:
            continue
        d.fields["closing_date"] = c
        # The listing itself is an official application portal (e.g. NSP) and the card states that student
        # applications are open on it -> that sentence is the evidence for the application route.
        if is_application_platform(url) and re.search(r"application", c.evidence, re.I):
            d.fields["application_url"] = Extracted(
                {"url": url, "mode": "ONLINE", "route": "OFFICIAL"}, c.evidence, c.start, c.end, section="card",
                extractor="application_url:portal", pass_="SECTION")
        o = relocate(opening)
        if o:
            d.fields["opening_date"] = o
        d.consistency["closing_date"] = True
        if india_ev:
            p_in = doc.text.find(india_ev, npos)
            if p_in < 0:
                continue
            d.fields["india_eligibility"] = Extracted(True, india_ev, p_in, p_in + len(india_ev), section="card",
                                                      extractor="india_eligibility", pass_="SECTION")
        # facts stated in the official scheme name itself ("... For Girl Students", "... For SC Students",
        # "Post Matric ...", "Post Graduate ...")
        name_ev = d.fields["name"]
        if NAME_FEMALE.search(name):
            d.fields["gender"] = Extracted("FEMALE", name, npos, npos + len(name), extractor="gender:name",
                                           pass_="SECTION")
        cats = extract_categories(doc, [], name_ev)
        if cats:
            d.fields["categories"] = Extracted([c.value for c in cats], cats[0].evidence, cats[0].start, cats[0].end,
                                               extractor="category:name", pass_="SECTION", extra={"all": cats})
        lv = _levels_in(name)
        if lv:
            d.fields["education_levels"] = Extracted(lv, name, npos, npos + len(name), extractor="education:name",
                                                     pass_="SECTION")
        m = PROVIDER_PREFIX.match(name)
        if m:
            d.fields["provider"] = Extracted(m.group(1), m.group(1), npos, npos + len(m.group(1)),
                                             extractor="provider:card-prefix", pass_="META")
        d.funding_type = funding_type_of(name)
        detail = None
        for a in container.iter("a"):
            href = (a.get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:")) and DETAIL_LINK.search(norm_ws(a.text_content()) or ""):
                absu = urljoin(url, href)
                if re.search(r"/(null|undefined|none)/?$", absu, re.I):
                    continue          # JS placeholder, not a document
                if registered_domain(absu) == registered_domain(url) and "faq" not in absu.lower():
                    detail = absu
                    break
        out.append((d, detail))
    return out
