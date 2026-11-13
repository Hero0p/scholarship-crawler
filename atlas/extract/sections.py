"""Heading detection and mapping to canonical sections (eligibility, benefits, dates, ...)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from atlas import config
from atlas.extract.content import Block, Document

NUMBERING_RE = re.compile(r"^\s*(?:\(?[0-9ivxIVX]{1,4}[.)]|\(?[a-hA-H][.)]|[-•*►▪·])\s*")
LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


@dataclass
class Section:
    name: str
    heading: Block
    blocks: list[Block]
    inline: bool = False          # "Eligibility: ..." style (label + content in one block)

    @property
    def start(self) -> int:
        return self.heading.start

    @property
    def end(self) -> int:
        return (self.blocks[-1] if self.blocks else self.heading).end

    def text(self, doc: Document) -> str:
        return doc.text[self.start:self.end]

    def content_text(self, doc: Document) -> str:
        if self.inline:
            return doc.text[self.start:self.end]
        if not self.blocks:
            return ""
        return doc.text[self.blocks[0].start:self.end]


def _clean_heading(t: str) -> str:
    t = NUMBERING_RE.sub("", t).strip().strip(":-–|").strip().lower()
    return re.sub(r"\s+", " ", t)


_SYN: list[tuple[str, str]] | None = None


def _synonyms() -> list[tuple[str, str]]:
    global _SYN
    if _SYN is None:
        pairs = []
        for sec, syns in config.keywords()["sections"].items():
            for s in syns:
                pairs.append((s.lower(), sec))
        pairs.sort(key=lambda p: -len(p[0]))
        _SYN = pairs
    return _SYN


def match_section(heading: str) -> str | None:
    h = _clean_heading(heading)
    if not h or len(h) > 70:
        return None
    for syn, sec in _synonyms():
        if h == syn:
            return sec
    for syn, sec in _synonyms():
        if len(syn) >= 5 and h.startswith(syn) and len(h) <= len(syn) + 25:
            return sec
        if len(syn) >= 8 and fuzz.ratio(h, syn) >= 90:
            return sec
    return None


def is_heading_like(b: Block) -> bool:
    if b.tag in LEVEL or b.tag in ("dt", "th"):
        return len(b.text) <= 120
    if b.bold and len(b.text) <= 90:
        return True
    if len(b.text) <= 60 and b.text.rstrip().endswith(":"):
        return True
    # plain blocks only count as headings on an EXACT synonym ("Benefits"), never on a prefix
    # ("Stipend 500/- Rs per month" is content, not a heading)
    h = _clean_heading(b.text)
    return len(b.text) <= 45 and any(h == syn for syn, _ in _synonyms())


def _inline_label(b: Block) -> str | None:
    """'Eligibility: Students of ...' or table row 'Eligibility | ...'."""
    if b.tag == "tr" and " | " in b.text:
        return match_section(b.text.split(" | ", 1)[0])
    m = re.match(r"^(.{3,45}?)\s*[:\-–]\s+\S", b.text)
    if m and len(b.text) > len(m.group(1)) + 15:
        return match_section(m.group(1))
    return None


def segment(doc: Document, max_blocks: int = 45, min_start: int = 0) -> dict[str, Section]:
    """Return the FIRST occurrence of each canonical section (only from `min_start` onward)."""
    out: dict[str, Section] = {}
    blocks = [b for b in doc.blocks if b.start >= min_start]
    i = 0
    while i < len(blocks):
        b = blocks[i]
        sec = None
        inline = False
        if is_heading_like(b):
            sec = match_section(b.text)
        if sec is None:
            sec = _inline_label(b)
            inline = sec is not None
        if sec is None:
            i += 1
            continue
        level = LEVEL.get(b.tag, 7)
        body: list[Block] = []
        j = i + 1
        if not inline or b.tag != "tr":
            while j < len(blocks) and len(body) < max_blocks:
                nb = blocks[j]
                if is_heading_like(nb) and (match_section(nb.text) or LEVEL.get(nb.tag, 99) <= level):
                    break
                if _inline_label(nb):
                    break
                body.append(nb)
                j += 1
        if sec not in out and (body or inline):
            out[sec] = Section(name=sec, heading=b, blocks=body, inline=inline)
        i = j if j > i else i + 1
    return out
