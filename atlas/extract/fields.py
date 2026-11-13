"""Rule-based field extractors. No generative models.

Every extractor returns `Extracted` objects whose `evidence` is a verbatim substring of
`Document.text` (offsets `start`/`end`), or None when the source does not support a value
("Not specified"). Extractors are deliberately conservative: a missed value costs confidence,
a wrong value is a serious failure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from atlas.extract.content import Document
from atlas.extract.sections import Section

# ----------------------------------------------------------------------------- data


@dataclass
class Extracted:
    value: object
    evidence: str
    start: int
    end: int
    kind: str = "TEXT"            # TEXT | LINK | REGISTRY | URL
    section: str | None = None
    extractor: str = ""
    pass_: str = "SECTION"        # SECTION | FULLTEXT | META
    extra: dict = field(default_factory=dict)


def span_ev(doc: Document, start: int, end: int, value, extractor: str, section: str | None,
            pass_: str, max_len: int = 700, **extra) -> Extracted:
    """Evidence = the block(s) covering [start, end), trimmed to max_len around the match."""
    b0 = doc.block_at(start)
    b1 = doc.block_at(max(start, end - 1))
    s = b0.start if b0 else start
    e = b1.end if b1 else end
    if e - s > max_len:
        s = max(s, start - max_len // 3)
        e = min(e, max(end, s + max_len))
    return Extracted(value=value, evidence=doc.text[s:e], start=s, end=e, section=section,
                     extractor=extractor, pass_=pass_, extra=extra)


# ----------------------------------------------------------------------------- name

NAME_TERM = re.compile(r"\b(scholarships?|fellowships?|schemes?|awards?|grants?|programmes?|programs?|"
                       r"yojana|stipends?|bursar(?:y|ies)|chhatravritti|prize)\b", re.I)
GENERIC = {"scholarship", "scholarships", "fellowship", "fellowships", "scheme", "schemes", "award",
           "awards", "grant", "grants", "programme", "programmes", "program", "programs", "for", "the",
           "of", "and", "students", "student", "list", "all", "various", "details", "apply", "online",
           "portal", "home", "welcome", "page", "national", "india", "indian", "government", "govt",
           "about", "our", "other", "latest", "new", "open", "current", "available", "info",
           "information", "opportunities", "financial", "aid", "assistance", "education",
           "educational", "www", "com", "org", "in", "to", "a", "an", "on", "at", "by", "with",
           "development", "student's", "students'", "faq", "faqs", "guidelines", "notice", "notices",
           "notification", "circular", "important", "links", "link", "click", "here", "more", "read",
           "view", "download", "form", "forms", "status", "result", "results", "public", "user",
           "manual", "website", "official", "site", "is", "are", "how", "apply", "eligibility",
           "criteria", "benefits", "process", "application", "applications", "procedure", "under",
           "university", "college", "institute", "dashboard", "search", "category", "learning",
           "schemes", "&", "-", "|", "ministry", "department", "dept", "affairs", "welfare",
           "higher", "school", "technical", "council", "commission", "foundation", "trust", "value", "values",
           "amount", "deadline", "deadlines", "date", "dates", "closing", "opening", "find", "fees", "funding",
           "overview", "summary", "type", "types", "level", "levels", "duration", "number", "tenure", "terms",
           "conditions", "documents", "required", "contact", "us", "who", "can", "what", "when", "where",
           "your", "my", "this", "these", "key", "facts", "highlights", "new", "upcoming", "previous", "past"}
BAD_NAME = re.compile(r"\b(result|merit list|selected candidates|list of|faq|guidelines? for|"
                      r"notice|circular|corrigendum|tender|minutes|report|press|annual|sanction|"
                      r"release of|disbursement|verification of|schemes for|scholarship schemes|"
                      r"page not found|error|login|registration form|user manual|how to|"
                      r"frequently asked|advertisement|walk-in|recruitment|last date|extended|extension|"
                      r"date of|dates? for|opens? (?:on|from)|portal (?:for|is)|"
                      # institutional (not student-facing) schemes
                      r"conferences?|seminars?|workshops?|modrobs|laborator(?:y|ies)|idea lab|infrastructure|"
                      r"faculty|teachers?|institutional|collaborative research|research promotion|start-?ups?|"
                      r"incubat\w*|patents?|margdarshan|spices|gainer|quality improvement|capacity building|"
                      r"vaani|accreditation|mentoring of institutions|chair professor)\b", re.I)


SENTENCE_LIKE = re.compile(r"^(?:students?|candidates?|applicants?|this|these|award value|value|am|do|does|can|"
                           r"eligib\w*|if|for more|please|click|note|health)\b|\b(?:whose|who|which|should|must|will|"
                           r"is|are|was|were|has|have|can|may|receive[sd]?|get|shortlisted)\b", re.I)
LISTING_WORDS = re.compile(r"\b(database|finder|search|directory|catalogue|forms?|clos(?:e|es|ed|ing)|interviews?|"
                           r"shortlist\w*|celebrat\w*|calendar|announc\w*|congratulat\w*|webinar|news)\b", re.I)


def name_ok(name: str) -> bool:
    name = name.strip().rstrip(".").strip()          # official headings often end with a full stop
    if not (6 <= len(name) <= 180) or not NAME_TERM.search(name) or BAD_NAME.search(name):
        return False
    if "\n" in name or len(name.split()) > 24 or SENTENCE_LIKE.search(name) or LISTING_WORDS.search(name):
        return False
    if name.endswith("?"):
        return False          # questions
    if re.search(r"\b(to|for|of|and|the|in|on|with|by)$", name.rstrip(), re.I):
        return False          # truncated heading ("... Merit Scholarship to")
    toks = re.findall(r"[A-Za-z][A-Za-z'.]*", name.lower())
    meaningful = [t for t in toks if t.strip(".'") not in GENERIC and len(t.strip(".'")) >= 2]
    return len(meaningful) >= 1


def _title_segments(title: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s+[|–\-:»·]\s+|\s*\|\s*", title) if p.strip()]


def extract_name(doc: Document) -> Extracted | None:
    cands: list[tuple[str, str]] = [(h, "h1") for h in doc.h1[:3]]
    if doc.og_title:
        cands += [(seg, "og:title") for seg in _title_segments(doc.og_title)]
    if doc.title:
        cands += [(seg, "title") for seg in _title_segments(doc.title)]
    if doc.kind == "PDF":
        cands += [(b.text, "pdf-heading") for b in doc.blocks[:12] if len(b.text) <= 140]
    for cand, how in cands:
        cand = re.sub(r"\s+", " ", cand).strip(" .:-|")
        cand = re.sub(r"^(?:apply|register)(?: now| online)? (?:to|for) (?:the )?", "", cand, flags=re.I)
        # document titles: "INSPIRE Fellowship Guidelines (September, 2018)" -> "INSPIRE Fellowship"
        cand = re.sub(r"^(?:scheme )?guidelines? (?:of|for) (?:the )?", "", cand, flags=re.I)
        cand = re.sub(r"\s+(?:scheme )?guidelines?\b.*$", "", cand, flags=re.I).strip(" .:-|")
        if not name_ok(cand):
            continue
        pos = doc.text.find(cand)
        if pos < 0:
            continue
        return Extracted(value=cand, evidence=cand, start=pos, end=pos + len(cand),
                         extractor=f"name:{how}", pass_="META")
    return None


# ----------------------------------------------------------------------------- money

AMOUNT_RE = re.compile(
    r"(?:₹|Rs\.?|INR|Rupees)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakhs?|lacs?|crores?|k\b|/-)?"
    r"(?:\s*/-)?(?:\s*\(?\s*(?:per|p\.?)\s*(annum|year|month|semester|a\.|m\.)|"
    r"\s*(annually|yearly|monthly|one[- ]time|lump ?sum|p\.a\.?|p\.m\.?|pa\b|pm\b))?",
    re.I)
AWARD_CTX = re.compile(r"scholarship|award|stipend|fellowship|grant|assistance|benefit|amount|"
                       r"per annum|per month|prize|financial support|support of|receive|tuition|"
                       r"fees? (?:will be|shall be)? ?(?:reimbursed|paid|covered|waived)|maintenance allowance", re.I)
AMOUNT_RE2 = re.compile(r"(?<![\d,.])([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakhs?|lacs?|crores?)?\s*(?:/-)?\s*(?:Rs\.?|rupees|INR)(?![a-z])"
                        r"(?:\s*(?:per|p\.?)\s*(annum|year|month|semester|a\.|m\.)|\s*(annually|yearly|monthly|one[- ]time|lump ?sum))?", re.I)
NOT_AWARD_CTX = re.compile(r"income|salary|turnover|application fee|registration fee|processing fee|"
                           r"loan|penalty|fine|earning|wage|budget|crore was|allocated|outlay|bond|"
                           r"deposit|cost of", re.I)


def parse_inr(num: str, unit: str | None) -> int | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    u = (unit or "").lower()
    if u.startswith(("lakh", "lac")):
        v *= 100_000
    elif u.startswith("crore"):
        v *= 10_000_000
    elif u == "k":
        v *= 1000
    return int(round(v))


def _period(m: re.Match) -> str | None:
    p = (m.group(3) or m.group(4) or "").lower()
    if not p:
        return None
    if p.startswith(("annum", "year", "a.", "annual", "yearly", "p.a", "pa")):
        return "YEAR"
    if p.startswith(("month", "m.", "p.m", "pm")):
        return "MONTH"
    if p.startswith("semester"):
        return "SEMESTER"
    return "ONE_TIME"


def extract_amount(doc: Document, sections: dict[str, Section], lo: int = 0) -> tuple[Extracted | None, Extracted | None]:
    """(section pass, full-text pass)."""
    def scan(lo: int, hi: int, need_ctx: bool, pass_: str, sec: str | None):
        hits = []
        ms = sorted(list(AMOUNT_RE.finditer(doc.text, lo, hi)) + list(AMOUNT_RE2.finditer(doc.text, lo, hi)),
                    key=lambda x: x.start())
        for m in ms:
            v = parse_inr(m.group(1), m.group(2))
            if v is None or not (500 <= v <= 10_000_000):
                continue
            ctx = _block_window(doc, m.start(), m.end(), 160, 80)
            if NOT_AWARD_CTX.search(_block_window(doc, m.start(), m.end(), 70, 30)):
                continue
            if need_ctx and not AWARD_CTX.search(ctx):
                continue
            hits.append((m, v))
        if not hits:
            return None
        first_block = doc.block_at(hits[0][0].start())
        # keep hits within <= 4 consecutive blocks from the first one (one coherent evidence span)
        keep = [h for h in hits if h[0].start() - hits[0][0].start() < 600]
        vals = [v for _, v in keep]
        last = keep[-1][0]
        ev = span_ev(doc, hits[0][0].start(), last.end(), None, "amount", sec, pass_)
        periods = {_period(m) for m, _ in keep} - {None}
        value = {"min": min(vals), "max": max(vals),
                 "period": periods.pop() if len(periods) == 1 else None,
                 "text": first_block.text if first_block and len(first_block.text) <= 300 else
                 doc.text[hits[0][0].start(): hits[0][0].end()]}
        ev.value = value
        return ev

    a = None
    if "benefits" in sections:
        s = sections["benefits"]
        a = scan(s.start, s.end, False, "SECTION", "benefits")
    b = scan(lo, len(doc.text), True, "FULLTEXT", None)
    return a, b


# ----------------------------------------------------------------------------- dates

MONTHS = {m: i for i, names in enumerate(
    [[], ["jan", "january"], ["feb", "february"], ["mar", "march"], ["apr", "april"], ["may"],
     ["jun", "june"], ["jul", "july"], ["aug", "august"], ["sep", "sept", "september"],
     ["oct", "october"], ["nov", "november"], ["dec", "december"]]) for m in names}
MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RES = [
    ("DMY_TEXT", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-.,/]*(?:of\s+)?" + MON + r"\.?[\s\-.,/']*(\d{4})\b", re.I)),
    ("MDY_TEXT", re.compile(r"\b" + MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)),
    ("DMY_NUM", re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")),
    ("ISO", re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")),
]
CLOSING_CTX = re.compile(r"(last|closing|end|final|due)\s*date|deadline|apply\s+(?:before|by|till|until|on or before)|"
                         r"applications?\s+(?:will\s+)?(?:close|closes|closing|ends?|accepted till)|on or before|"
                         r"last day|closes on|till|until|extended (?:up ?to|till|until)|submission", re.I)
OPENING_CTX = re.compile(r"(opening|start|starting|commencement)\s*date|applications?\s+(?:will\s+)?(?:open|opens|start|starts)|open from|"
                         r"portal (?:will )?opens?|opens? on|start(?:s|ing)? (?:from|on)|commence", re.I)
NOT_STUDENT_DEADLINE = re.compile(r"verification|verify|institute level|institution level|\bINO\b|\bDNO\b|\bSNO\b|nodal|"
                                  r"result|exam(?:ination)? date|test date|interview|announce|declaration|disburs|"
                                  r"defective|re-?open(?:ing)? of defective|hard ?copy|mentor|tender|bid", re.I)
EXTENDED = re.compile(r"\bextend(?:ed|ing)?\b", re.I)


@dataclass
class DateHit:
    d: date
    start: int
    end: int


def find_dates(text: str, lo: int = 0, hi: int | None = None) -> list[DateHit]:
    hi = len(text) if hi is None else hi
    hits: dict[int, DateHit] = {}
    for kind, rx in DATE_RES:
        for m in rx.finditer(text, lo, hi):
            try:
                if kind == "DMY_TEXT":
                    d = date(int(m.group(3)), MONTHS[m.group(2).lower()[:3] if m.group(2).lower()[:4] != "sept" else "sep"], int(m.group(1)))
                elif kind == "MDY_TEXT":
                    d = date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
                elif kind == "DMY_NUM":
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                else:
                    d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, KeyError):
                continue
            if not (2015 <= d.year <= date.today().year + 2):
                continue
            if m.start() not in hits:
                hits[m.start()] = DateHit(d, m.start(), m.end())
    return sorted(hits.values(), key=lambda h: h.start)


def _label_context(doc: Document, h: DateHit) -> str:
    """Text before the date inside the same block (or the label cell of a table row)."""
    b = doc.block_at(h.start)
    lo = b.start if b else max(0, h.start - 150)
    ctx = doc.text[max(lo, h.start - 150): h.start]
    if b and b.tag == "tr":
        ctx = doc.text[b.start: h.start]
    elif b and len(ctx.strip()) < 4:
        # date alone in its block: use previous block as the label (e.g. definition lists)
        idx = doc.blocks.index(b)
        if idx > 0:
            ctx = doc.blocks[idx - 1].text[-150:] + " " + ctx
    return ctx


def extract_dates(doc: Document, sections: dict[str, Section], lo: int = 0):
    """Returns (closing_A, closing_B, opening) Extracted-or-None."""
    def pick(lo, hi, pass_, sec):
        closing, opening = [], []
        for h in find_dates(doc.text, lo, hi):
            ctx = _label_context(doc, h)
            ctx_tail = ctx[-110:]
            if NOT_STUDENT_DEADLINE.search(ctx_tail):
                continue
            if CLOSING_CTX.search(ctx_tail):
                closing.append((h, bool(EXTENDED.search(ctx_tail))))
            elif OPENING_CTX.search(ctx_tail):
                opening.append(h)
        c = None
        if closing:
            ext = [x for x in closing if x[1]]
            h, is_ext = (max(ext, key=lambda x: x[0].d) if ext else closing[0])
            c = span_ev(doc, h.start, h.end, h.d.isoformat(), "closing_date", sec, pass_, max_len=400,
                        extended=is_ext)
        o = None
        if opening:
            h = opening[0]
            o = span_ev(doc, h.start, h.end, h.d.isoformat(), "opening_date", sec, pass_, max_len=400)
        return c, o

    ca = oa = None
    if "dates" in sections:
        s = sections["dates"]
        ca, oa = pick(s.start, s.end, "SECTION", "dates")
    cb, ob = pick(lo, len(doc.text), "FULLTEXT", None)
    return ca, cb, oa or ob


ROLLING = re.compile(r"open (?:throughout|all) the year|rolling basis|no (?:fixed )?deadline|"
                     r"accepted throughout the year|applications? can be submitted any ?time", re.I)
ACAD_YEAR = re.compile(r"\b(20\d{2})\s*[-–/]\s*(20\d{2}|\d{2})\b")
STATUS_OPEN = re.compile(r"applications? (?:are |is )?(?:now )?(?:open|invited)|registrations? (?:are |is )?(?:now )?open|"
                         r"portal is (?:now )?open|apply (?:now|online) (?:for|till|before)", re.I)
STATUS_CLOSED = re.compile(r"applications? (?:are |is |has been |have been )?(?:now )?closed|registrations? (?:are |is )?(?:now )?closed|"
                           r"last date (?:is |has )?(?:over|passed|expired)|closed for (?:the )?(?:session|year|academic)|"
                           r"no longer accepting", re.I)
STATUS_UPCOMING = re.compile(r"(?:applications?|registrations?|portal) (?:will (?:be )?open|opening soon)|coming soon", re.I)
DISCONTINUED = re.compile(r"\b(?:scheme|programme|program|scholarship)s? (?:has been|have been|is|are|stands|was) "
                          r"(?:now )?(?:discontinued|withdrawn|scrapped|closed permanently)\b|"
                          r"\bno longer (?:available|offered)\b", re.I)


def text_quality(text: str) -> float:
    """Share of tokens that look like OCR letter/digit confusions ('2O2l', 'l5th', 't00,000').
    Low-quality text layers are never used for values."""
    toks = re.findall(r"[A-Za-z0-9,]{3,}", text)
    if not toks:
        return 1.0
    bad = sum(1 for t in toks if re.search(r"\d[OolIS]\d|[OolI]\d{2}|\d{2}[OolI]\b|^[a-z]\d{2}", t))
    return 1.0 - bad / len(toks)


def extract_rolling(doc: Document, lo: int = 0) -> Extracted | None:
    m = ROLLING.search(doc.text, lo)
    return span_ev(doc, m.start(), m.end(), True, "rolling", None, "FULLTEXT", 300) if m else None


def extract_academic_year(doc: Document, name: str | None, lo: int = 0) -> Extracted | None:
    best = None
    for m in ACAD_YEAR.finditer(doc.text, lo):
        y1 = int(m.group(1))
        y2 = m.group(2)
        y2i = int(y2) if len(y2) == 4 else 2000 + int(y2)
        if y2i != y1 + 1 or not (2015 <= y1 <= date.today().year + 1):
            continue
        if best is None or y1 > best[0]:
            best = (y1, m)
    if not best:
        return None
    y1, m = best
    return span_ev(doc, m.start(), m.end(), f"{y1}-{str(y1 + 1)[2:]}", "academic_year", None, "FULLTEXT", 300)


def extract_page_status(doc: Document, lo: int = 0) -> Extracted | None:
    for rx, val in ((DISCONTINUED, "DISCONTINUED"), (STATUS_CLOSED, "CLOSED"), (STATUS_OPEN, "OPEN"),
                    (STATUS_UPCOMING, "UPCOMING")):
        m = rx.search(doc.text, lo)
        if m:
            return span_ev(doc, m.start(), m.end(), val, "page_status", None, "FULLTEXT", 300)
    return None


# ----------------------------------------------------------------------------- eligibility

ELIG_CUE = re.compile(r"\b(eligib\w*|must|should|candidates?|applicants?|open to|only|students? who|"
                      r"students? (?:of|from|belonging|studying|pursuing|enrolled)|criteria|requirement)\b", re.I)


def eligibility_scopes(doc: Document, sections: dict[str, Section], lo: int = 0):
    """[(lo, hi, pass, section)] ; pass A = eligibility section, pass B = cue sentences in full text."""
    a = []
    if "eligibility" in sections:
        s = sections["eligibility"]
        a.append((s.start, s.end, "SECTION", "eligibility"))
    b = []
    for blk in doc.blocks:
        if blk.start >= lo and ELIG_CUE.search(blk.text) and len(blk.text) <= 1200:
            b.append((blk.start, blk.end, "FULLTEXT", None))
    return a, b


def _block_window(doc: Document, start: int, end: int, before: int, after: int) -> str:
    """Context around a match, never crossing the boundaries of the block that contains it."""
    b = doc.block_at(start)
    lo = max(b.start if b else 0, start - before)
    hi = min(b.end if b else len(doc.text), end + after)
    return doc.text[lo:hi]


NEG_HEAD = re.compile(r"\b(not (?:be )?eligible|ineligib\w*|shall not be (?:eligible|considered|entitled)|"
                      r"will not be (?:eligible|considered)|are not entitled|not entitled|exclusions?|excluded|"
                      r"debarred|disqualif\w*)\b", re.I)
TOP_ITEM = re.compile(r"^(?:\d{1,2}\.\s+\S|\(?[a-h]\)\s|[A-Z][A-Z &/]{4,}:?$)")


def exclusion_spans(doc: Document) -> list[tuple[int, int]]:
    """Text ranges listing who is NOT eligible ("The following are not eligible: (i) ... (ii) ...").
    Values inside them are exclusions and must never be read as eligibility criteria."""
    cached = doc.__dict__.get("_excl")
    if cached is not None:
        return cached
    spans = []
    blocks = doc.blocks
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if NEG_HEAD.search(b.text):
            start, end = b.start, b.end
            if b.text.rstrip().endswith((":", "-")) or len(b.text) < 140:
                j = i + 1
                while j < len(blocks) and j - i <= 25 and not TOP_ITEM.match(blocks[j].text):
                    end = blocks[j].end
                    j += 1
                i = j - 1
            spans.append((start, end))
        i += 1
    doc.__dict__["_excl"] = spans
    return spans


def in_exclusion(doc: Document, pos: int) -> bool:
    return any(s <= pos < e for s, e in exclusion_spans(doc))


def _first(doc, scopes, rx, conv, extractor, max_len=500, flags_ctx=None):
    for lo, hi, pass_, sec in scopes:
        for m in rx.finditer(doc.text, lo, hi):
            if in_exclusion(doc, m.start()):
                continue
            if flags_ctx and flags_ctx.search(_block_window(doc, m.start(), m.end(), 60, 40)):
                continue
            v = conv(m)
            if v is None:
                continue
            return span_ev(doc, m.start(), m.end(), v, extractor, sec, pass_, max_len)
    return None


INCOME_RE = re.compile(
    r"income[^.;\n]{0,140}?(?:not (?:exceed\w*|be more than|more than)|does not exceed|less than|below|up ?to|"
    r"within|maximum(?: of)?|max\.?|ceiling(?: of)?|limit(?: of)?|≤|<|not above|should be|of)\s*(?:of\s*)?"
    r"(?:₹|Rs\.?|INR|Rupees)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakhs?|lacs?|crores?|/-)?", re.I)
INCOME_RE2 = re.compile(r"(?:₹|Rs\.?|INR)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(lakhs?|lacs?|crores?|/-)?[^.;\n]{0,40}?"
                        r"(?:per annum|p\.a\.|annual(?:ly)?)?[^.;\n]{0,30}?(?:family|parental|annual) income", re.I)


def extract_income(doc, scopes):
    def conv(m):
        v = parse_inr(m.group(1), m.group(2))
        return v if v and 10_000 <= v <= 10_000_000 else None
    r = _first(doc, scopes, INCOME_RE, conv, "income")
    return r or _first(doc, scopes, INCOME_RE2, conv, "income")


AGE_RANGE = re.compile(r"\bage[^.;\n]{0,60}?(\d{1,2})\s*(?:years?)?\s*(?:to|-|and)\s*(\d{1,2})\s*years", re.I)
AGE_MAX = re.compile(r"(?:\bage[^.;\n]{0,50}?(?:not (?:exceed\w*|be more than|more than)|below|less than|under|up ?to|maximum(?: of)?)|"
                     r"(?:below|under|less than|not more than) the age of)\s*(\d{1,2})\s*years", re.I)
AGE_MIN = re.compile(r"\bage[^.;\n]{0,50}?(?:at least|minimum(?: of)?|not less than|above)\s*(\d{1,2})\s*years", re.I)


def extract_age(doc, scopes):
    r = _first(doc, scopes, AGE_RANGE,
               lambda m: {"min": int(m.group(1)), "max": int(m.group(2))}
               if 5 <= int(m.group(1)) < int(m.group(2)) <= 60 else None, "age")
    if r:
        return r
    r = _first(doc, scopes, AGE_MAX, lambda m: {"min": None, "max": int(m.group(1))}
               if 8 <= int(m.group(1)) <= 60 else None, "age")
    if r:
        return r
    return _first(doc, scopes, AGE_MIN, lambda m: {"min": int(m.group(1)), "max": None}
                  if 5 <= int(m.group(1)) <= 50 else None, "age")


# FEMALE only on restrictive phrasing; never from reservation / quota / definition sentences
FEMALE = re.compile(r"\b(only (?:for )?(?:girls?|women|female)|(?:girls?|women|female)(?: students| candidates| applicants)? only|"
                    r"exclusively for (?:girls?|women|female)|meant for (?:girls?|women)|open (?:only )?to (?:girls?|women|female)|"
                    r"(?:scholarship|fellowship|scheme|programme|program) (?:is )?for (?:girl students|girls|women|female students)|"
                    r"(?:applicant|candidate|student)s? (?:must|should) be (?:a )?(?:girl|woman|women|female))\b", re.I)
GENDER_NOT_RESTRICTIVE = re.compile(r"reserv|slots?|quota|%|half of|means|definition|preference|priority|in case", re.I)
ALLGENDER = re.compile(r"\b(boys and girls|girls and boys|both (?:male and female|boys and girls)|irrespective of gender|all genders?)\b", re.I)
TRANS = re.compile(r"\btransgender\b", re.I)


NAME_FEMALE = re.compile(r"\b(women'?s?|girls?'?|female|kanya|beti|mahila|ladies|daughters?)\b", re.I)


def extract_gender(doc, scopes, name_ev: Extracted | None):
    if name_ev and NAME_FEMALE.search(str(name_ev.value)):
        return Extracted("FEMALE", name_ev.evidence, name_ev.start, name_ev.end, section=None,
                         extractor="gender:name", pass_="SECTION")
    r = _first(doc, scopes, ALLGENDER, lambda m: "ALL", "gender")
    if r:
        return r
    return _first(doc, scopes, FEMALE, lambda m: "FEMALE", "gender", flags_ctx=GENDER_NOT_RESTRICTIVE)


CATEGORY_RES = [
    ("SC", re.compile(r"\bSCs?\b|\bScs\b"), re.compile(r"scheduled castes?", re.I)),
    ("ST", re.compile(r"\bSTs?\b"), re.compile(r"scheduled tribes?", re.I)),
    ("OBC", re.compile(r"\bOBCs?\b"), re.compile(r"other backward class(?:es)?", re.I)),
    ("EWS", re.compile(r"\bEWS\b"), re.compile(r"economically weaker sections?", re.I)),
    ("DNT", re.compile(r"\bDNTs?\b"), re.compile(r"de-?notified tribes?", re.I)),
    ("MINORITY", re.compile(r"$^"), re.compile(r"\bminorit(?:y|ies)\b|\b(?:muslim|christian|sikh|buddhist|jain|parsi)s?\b", re.I)),
    ("PWD", re.compile(r"\bPwDs?\b|\bPWD\b|\bCWSN\b"), re.compile(r"disabilit(?:y|ies)|differently[- ]abled|divyang|specially[- ]abled|handicapped", re.I)),
    ("DEFENCE", re.compile(r"\bCAPF\b"), re.compile(r"ex-?servicem[ae]n|armed forces|defence personnel|wards of (?:serving|ex)|paramilitary|martyr", re.I)),
    ("SPORTS", re.compile(r"$^"), re.compile(r"sports ?persons?|sports quota|outstanding sports|sports achievers?|sportsmen|sportswomen", re.I)),
    ("ORPHAN", re.compile(r"$^"), re.compile(r"\borphans?\b", re.I)),
]
RESERVATION_CTX = re.compile(r"reserv|quota|as per (?:government|govt|goi)|relaxation|norms", re.I)
# a category is a CRITERION only when the sentence restricts eligibility to it
CATEGORY_RESTRICT = re.compile(r"belong(?:ing|s)? to|only|exclusively|students? (?:from|of)|candidates? (?:from|of)|"
                               r"applicants? (?:from|of)|should be|must be|caste|tribe|community|communities|"
                               r"category|categories|wards? of|children of|persons? with|for (?:the )?(?:students|candidates)",
                               re.I)
CATEGORY_CROSSREF = re.compile(r"also eligible|other schemes?|another scheme|not eligible|cannot avail|"
                               r"in addition to|any other scholarship|including\b", re.I)


def extract_categories(doc, scopes, name_ev: Extracted | None):
    found: dict[str, Extracted] = {}
    texts = []
    if name_ev:
        texts.append((name_ev.start, name_ev.end, "NAME", None))
    texts += scopes
    for lo, hi, pass_, sec in texts:
        for cat, abbr, full in CATEGORY_RES:
            if cat in found:
                continue
            for rx in (abbr, full):
                m = rx.search(doc.text, lo, hi)
                if not m or (pass_ != "NAME" and in_exclusion(doc, m.start())):
                    continue
                sentence = _block_window(doc, m.start(), m.end(), 160, 160)
                if pass_ != "NAME" and (not CATEGORY_RESTRICT.search(sentence) or CATEGORY_CROSSREF.search(sentence)):
                    continue
                label = cat + (" (reservation)" if RESERVATION_CTX.search(sentence) else "")
                found[cat] = span_ev(doc, m.start(), m.end(), label, "category", sec,
                                     "SECTION" if pass_ == "NAME" else pass_, 400)
                break
    return list(found.values())


DISABILITY_PCT = re.compile(r"(\d{2})\s?%\s*(?:or (?:more|above)\s*)?(?:of\s*)?(?:benchmark\s*)?disabilit|"
                            r"disabilit\w*\s*(?:of|not less than|at least|minimum(?: of)?)\s*(\d{2})\s?%", re.I)


def extract_disability(doc, scopes):
    return _first(doc, scopes, DISABILITY_PCT,
                  lambda m: int(m.group(1) or m.group(2)) if 20 <= int(m.group(1) or m.group(2)) <= 100 else None,
                  "disability_pct")


PCT_RE = re.compile(r"(?:minimum(?: of)?|at least|atleast|not less than|secured|scored|obtained|with|above|more than)\s*"
                    r"(\d{2}(?:\.\d{1,2})?)\s?%|(\d{2}(?:\.\d{1,2})?)\s?%\s*(?:marks|aggregate|or above|and above|or more|in (?:class|the|their|12|10))", re.I)
PCT_BAD = re.compile(r"attendance|disabilit|income|reserv|seats|quota|weightage|interest|subsidy|of the (?:fee|cost)|tuition|share|"
                     r"admission|institutions?|placement|slots?", re.I)
MARKS_CTX = re.compile(r"marks|aggregate|percentage|score|grade|class|examination|exam\b|qualifying|secured|obtained|"
                       r"board|cgpa|result|merit", re.I)
CGPA_RE = re.compile(r"(?:CGPA|SGPA|CPI|GPA)\s*(?:of\s*)?(?:at least|minimum(?: of)?|not less than|above)?\s*(\d(?:\.\d{1,2})?)|"
                     r"(\d(?:\.\d{1,2})?)\s*(?:CGPA|SGPA|CPI)", re.I)


def extract_academic(doc, scopes):
    def conv(m):
        v = float(m.group(1) or m.group(2))
        if not 33 <= v <= 100 or not MARKS_CTX.search(_block_window(doc, m.start(), m.end(), 80, 60)):
            return None
        return v
    pct = _first(doc, scopes, PCT_RE, conv, "min_percentage", flags_ctx=PCT_BAD)
    cg = _first(doc, scopes, CGPA_RE,
                lambda m: float(m.group(1) or m.group(2)) if 4 <= float(m.group(1) or m.group(2)) <= 10 else None,
                "min_cgpa")
    return pct, cg


EDU_LEVELS = [
    ("PRE_MATRIC", re.compile(r"pre[- ]?matric", re.I)),
    ("POST_MATRIC", re.compile(r"post[- ]?matric", re.I)),
    ("CLASS_1_8", re.compile(r"class(?:es)?\s*(?:1|I)(?:st)?\s*(?:to|-)\s*(?:8|VIII)(?:th)?\b", re.I)),
    # abbreviations are matched case-sensitively via (?-i:...) — "B.E." must not match the word "be"
    ("CLASS_9_10", re.compile(r"\bclass(?:es)?\s*(?:9|10|IX|X)(?:th)?\b|\b(?:9|10)th\s*(?:class|standard|std|grade)|"
                              r"\b9th\s*(?:or|and|/|&)\s*10th\b|(?-i:\bSSC\b)", re.I)),
    ("CLASS_11_12", re.compile(r"\bclass(?:es)?\s*(?:11|12|XI|XII)(?:th)?\b|\b(?:11|12)th\s*(?:class|standard|std|grade)|"
                               r"\b11th\s*(?:or|and|/|&)\s*12th\b|higher secondary|senior secondary|"
                               r"intermediate|(?-i:\bHSC\b)|plus two|\+2\b", re.I)),
    ("DIPLOMA_ITI", re.compile(r"\bdiploma\b|polytechnic|(?-i:\bITI\b)", re.I)),
    ("UG", re.compile(r"under-?\s?graduat\w*|(?-i:\bUG\b)|bachelor'?s?|graduation|(?-i:B\.\s?Tech|\bBTech\b|\bB\.E\.?|\bBE\b(?=\s|/|,)|"
                      r"\bMBBS\b|\bBDS\b|B\.\s?Sc|\bBSc\b|B\.\s?Com|\bBCom\b|\bBBA\b|\bBCA\b|B\.\s?Pharm|\bLLB\b|B\.\s?Arch)|"
                      r"degree course|professional (?:degree|course)", re.I)),
    ("PG", re.compile(r"post-?\s?graduat\w*|(?-i:\bPG\b)|master'?s|(?-i:M\.\s?Tech|\bMTech\b|M\.\s?Sc|\bMSc\b|\bMBA\b|"
                      r"M\.\s?Com|\bMCA\b|M\.\s?Pharm|\bLLM\b|M\.\s?Arch|M\.\s?Phil)", re.I)),
    ("PHD", re.compile(r"Ph\.?\s?D|doctoral|doctorate|research scholars?|(?-i:\bJRF\b|\bSRF\b)", re.I)),
    ("POSTDOC", re.compile(r"post-?\s?doc(?:toral)?", re.I)),
]
STUDY_CUE = re.compile(r"(studying|pursuing|enrolled|admitted|admission|first year|1st year|students? (?:of|in)|"
                       r"currently in|in class|for (?:the )?(?:students|study|studies|pursuing)|"
                       r"courses? (?:like|such as)|programmes? (?:like|such as)|level)\W", re.I)


PREREQ = re.compile(r"\bpass(?:ed|ing)?\b|qualif\w*|lateral entry|completed|after (?:class|the)|cleared", re.I)


def _levels_in(text: str) -> list[str]:
    out = []
    for lvl, rx in EDU_LEVELS:
        if rx.search(text):
            out.append(lvl)
    if "POSTDOC" in out and "PHD" in out:
        # "post-doctoral" also matches the doctoral pattern
        if not re.search(r"(?<!post-)(?<!post)\b(?:Ph\.?\s?D|doctoral|doctorate)", text, re.I):
            out.remove("PHD")
    return out


def extract_education(doc, sections, name_ev: Extracted | None):
    """Pass A: name + study-cue phrases inside the eligibility section; pass B: whole eligibility section."""
    a = None
    if name_ev:
        lv = _levels_in(str(name_ev.value))
        if lv:
            a = Extracted(lv, name_ev.evidence, name_ev.start, name_ev.end, extractor="education:name", pass_="SECTION")
    if a is None and "eligibility" in sections:
        s = sections["eligibility"]
        levels: list[str] = []
        lo_ev = hi_ev = None
        for m in STUDY_CUE.finditer(doc.text, s.start, s.end):
            blk = doc.block_at(m.start())
            b_lo = blk.start if blk else m.start()
            w_hi = min(s.end, m.end() + 120, blk.end if blk else s.end)
            window = doc.text[m.start(): w_hi]
            if PREREQ.search(window) or in_exclusion(doc, m.start()):
                continue      # "have to pass Diploma", "after passing class 12": prerequisite, not level of study
            lv = _levels_in(window)
            if not lv:
                continue
            if lo_ev is not None and w_hi - lo_ev > 1200:
                break         # keep one contiguous evidence span that contains every contributing phrase
            levels += [x for x in lv if x not in levels]
            lo_ev = b_lo if lo_ev is None else lo_ev
            hi_ev = w_hi
        if levels:
            a = Extracted(levels, doc.text[lo_ev:hi_ev], lo_ev, hi_ev, section="eligibility", extractor="education",
                          pass_="SECTION")
    b = None
    if "eligibility" in sections:
        s = sections["eligibility"]
        # first block mentioning a level; the value is ONLY what that evidence block supports
        for blk in doc.blocks:
            if s.start <= blk.start < s.end and not in_exclusion(doc, blk.start) and not PREREQ.search(blk.text):
                lv = _levels_in(blk.text)
                if lv and len(blk.text) <= 700:
                    b = Extracted(lv, blk.text, blk.start, blk.end, section="eligibility", extractor="education",
                                  pass_="FULLTEXT")
                    break
    return a, b


STATES = ["Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
          "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
          "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Orissa", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
          "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir",
          "Jammu & Kashmir", "J&K", "J & K", "Ladakh", "Puducherry", "Pondicherry", "Chandigarh", "Andaman and Nicobar",
          "Lakshadweep", "Dadra and Nagar Haveli", "Daman and Diu"]
STATE_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in STATES) + r")\b", re.I)
DOMICILE_CUE = re.compile(r"domicile|domiciled|resident|native|permanent(?:ly)? resid", re.I)


def _canon_state(s: str) -> str:
    t = s.strip()
    if re.fullmatch(r"J\s?&\s?K|Jammu (?:&|and) Kashmir", t, re.I):
        return "Jammu and Kashmir"
    return {"orissa": "Odisha", "pondicherry": "Puducherry"}.get(t.lower(), t.title())


def extract_domicile(doc, scopes):
    for lo, hi, pass_, sec in scopes:
        for m in DOMICILE_CUE.finditer(doc.text, lo, hi):
            win_lo, win_hi = max(lo, m.start() - 100), min(hi, m.end() + 100)
            states = sorted({_canon_state(s.group(1)) for s in STATE_RE.finditer(doc.text, win_lo, win_hi)})
            if states:
                return span_ev(doc, win_lo, win_hi, states, "domicile", sec, pass_, 500)
    return None


INSTITUTION_RE = re.compile(r"(recogni[sz]ed|approved|affiliated)\s+(?:by|to|under|from)|AICTE[- ]approved|\bNIRF\b|"
                            r"government (?:or government[- ]aided |aided )?(?:schools?|colleges?|institutions?)|"
                            r"centrally funded|\bCFTIs?\b|\bIITs?\b|\bNITs?\b|\bIISERs?\b|top \d+ (?:universities|institutions)|"
                            r"institutions? of national importance", re.I)


def extract_institution(doc, sections):
    if "eligibility" not in sections:
        return None
    s = sections["eligibility"]
    m = INSTITUTION_RE.search(doc.text, s.start, s.end)
    if not m:
        return None
    ev = span_ev(doc, m.start(), m.end(), None, "institution", "eligibility", "SECTION", 400)
    ev.value = ev.evidence
    return ev


# ----------------------------------------------------------------------------- section texts

def section_text_field(doc: Document, sections: dict[str, Section], name: str, extractor: str,
                       max_len: int = 1500) -> Extracted | None:
    s = sections.get(name)
    if not s:
        return None
    body = list(s.blocks)
    while body and len(body[0].text) <= 2:      # stray close buttons ("X") / bullets
        body.pop(0)
    lo = s.start if s.inline else (body[0].start if body else s.start)
    hi = s.end
    if hi - lo > max_len:  # cut at a block boundary
        cut = lo
        for b in (s.blocks or [s.heading]):
            if b.end - lo > max_len:
                break
            cut = b.end
        hi = cut if cut > lo else lo + max_len
    txt = doc.text[lo:hi].strip()
    if len(txt) < 15:
        return None
    lo = doc.text.find(txt, lo)
    return Extracted(value=txt, evidence=txt, start=lo, end=lo + len(txt), section=name,
                     extractor=extractor, pass_="SECTION")


DOC_DICT = [("Aadhaar card", r"aadh?aar"), ("Income certificate", r"income certificate"),
            ("Caste certificate", r"caste certificate|community certificate"),
            ("Domicile certificate", r"domicile certificate|residence certificate"),
            ("Mark sheets", r"mark ?sheets?|marks? card|grade card|transcripts?"),
            ("Bank account details", r"bank (?:account|passbook|details)|cancelled cheque"),
            ("Photograph", r"photograph|passport[- ]size photo"),
            ("Bonafide/Enrollment certificate", r"bonafide|enrol?ment certificate|admission letter|fee receipt"),
            ("Disability certificate", r"disability certificate|UDID"),
            ("Recommendation letter", r"recommendation letters?|letters? of reference|referee"),
            ("Statement of purpose / essay", r"statement of purpose|\bSOP\b|essay"),
            ("CV / Resume", r"\bCV\b|curriculum vitae|resume")]


def extract_documents(doc, sections):
    f = section_text_field(doc, sections, "documents", "documents", 1200)
    if not f:
        return None
    items = [label for label, rx in DOC_DICT if re.search(rx, f.value, re.I)]
    f.extra["normalized"] = items
    return f


# ----------------------------------------------------------------------------- application url

APPLY_RE = re.compile(r"\b(apply(?: now| online| here| for)?|online application|application form|register(?: now| here| online)?|"
                      r"registration|submit (?:your )?application|start (?:your )?application|आवेदन)\b", re.I)
APPLY_BAD = re.compile(r"status|result|guideline|faq|manual|login help|forgot|renewal status|track|template|sample|"
                       r"guidance|how to|instructions?", re.I)


def extract_application(doc: Document, official_domain: str, raw_html: str | None):
    from atlas.classify.source_classifier import (is_aggregator, is_application_platform,
                                                  is_ignored_host, tld_class)
    from atlas.urls import registered_domain

    best, best_rank = None, 99
    for ln in doc.links:
        if not ln.text or len(ln.text) > 80 or not APPLY_RE.search(ln.text) or APPLY_BAD.search(ln.text):
            continue
        href = ln.href
        if not href.startswith("http") or is_ignored_host(href):
            continue
        reg = registered_domain(href)
        if href.lower().split("?")[0].endswith((".pdf", ".doc", ".docx")):
            rank, mode, route = 3, "OFFLINE", "OFFICIAL_FORM"
            if reg != official_domain:
                continue
        elif reg == official_domain or tld_class(href) == "GOV":
            rank, mode, route = 1, "ONLINE", "OFFICIAL"
        elif is_application_platform(href) or is_aggregator(href):
            rank, mode, route = 2, "ONLINE", "PLATFORM"
        else:
            rank, mode, route = 2, "ONLINE", "THIRD_PARTY"
        if not ln.in_content:
            rank += 0.5
        if rank < best_rank:
            best_rank, best = rank, (ln, mode, route)
    if not best:
        return None
    ln, mode, route = best
    pos = doc.text.find(ln.text)
    return Extracted(value={"url": ln.href, "mode": mode, "route": route}, evidence=f"[{ln.text}] -> {ln.href}",
                     start=pos, end=pos + len(ln.text) if pos >= 0 else -1, kind="LINK",
                     extractor="application_url", pass_="SECTION", extra={"anchor": ln.text})


# ----------------------------------------------------------------------------- provider

PROVIDER_LABEL = re.compile(r"^(?:Department Name|Name of (?:the )?Department|Implementing (?:Agency|Department|Ministry)|"
                            r"Nodal (?:Ministry|Department|Agency)|Offered by|Sponsored by|Provider|Ministry)\s*[:|\-]?\s*$",
                            re.I)


def extract_provider(doc: Document, registry_provider: str | None, source_name: str | None, lo: int = 0):
    # explicit label on the page ("Department Name" -> next block) is the most precise provider
    for i, b in enumerate(doc.blocks):
        if b.start < lo or i + 1 >= len(doc.blocks):
            continue
        if PROVIDER_LABEL.match(b.text):
            nxt = doc.blocks[i + 1]
            if 4 <= len(nxt.text) <= 120 and re.search(r"[A-Za-z]{3}", nxt.text):
                return Extracted(value=nxt.text, evidence=nxt.text, start=nxt.start, end=nxt.end,
                                 extractor="provider:label", pass_="META")
    cands = []
    if registry_provider:
        cands.append(registry_provider)
        cands.append(re.sub(r"\s*\(.*?\)", "", registry_provider))
        cands += re.findall(r"\(([^)]{2,20})\)", registry_provider)
        cands.append(registry_provider.split(",")[0])
    for c in [c.strip() for c in cands if c and len(c.strip()) >= 3]:
        pos = doc.text.lower().find(c.lower())
        if pos >= 0:
            return Extracted(value=registry_provider, evidence=doc.text[pos:pos + len(c)], start=pos,
                             end=pos + len(c), extractor="provider:text", pass_="META")
    if registry_provider:
        return Extracted(value=registry_provider, evidence=f"Source registry entry: {source_name} -> {registry_provider}",
                         start=-1, end=-1, kind="REGISTRY", extractor="provider:registry", pass_="META")
    # discovered (non-registry) source: og:site_name or copyright line
    m = re.search(r"(?:©|copyright)\s*(?:\d{4}\s*[-–]?\s*\d{0,4})?\s*,?\s*([A-Z][A-Za-z&.,' ]{3,80}?)(?:\.|\||All rights|$)", doc.text)
    if m:
        val = m.group(1).strip(" ,.")
        return Extracted(value=val, evidence=doc.text[m.start():m.end()], start=m.start(), end=m.end(),
                         extractor="provider:copyright", pass_="META")
    if doc.site_name:
        pos = doc.text.find(doc.site_name)
        if pos >= 0:
            return Extracted(value=doc.site_name, evidence=doc.site_name, start=pos, end=pos + len(doc.site_name),
                             extractor="provider:site_name", pass_="META")
    return None


# ----------------------------------------------------------------------------- India eligibility

INDIA_POS = re.compile(r"\b(Indian (?:nationals?|citizens?|students?|origin)|citizens? of India|nationals? of India|"
                       r"(?:students?|candidates?|applicants?) from India|resident of India|India\b)", re.I)
INDIA_NEG = re.compile(r"\b(foreign nationals?|international students only|non-Indian|for foreign students|"
                       r"not open to (?:Indian|citizens of India)|Indian nationals are not eligible)\b", re.I)


def india_eligible(doc: Document, domestic: bool) -> tuple[bool, Extracted | None]:
    neg = INDIA_NEG.search(doc.text)
    if neg and not domestic:
        return False, span_ev(doc, neg.start(), neg.end(), False, "india_eligibility", None, "FULLTEXT", 300)
    if domestic:
        return True, None
    m = INDIA_POS.search(doc.text)
    if m:
        return True, span_ev(doc, m.start(), m.end(), True, "india_eligibility", None, "FULLTEXT", 300)
    return False, None
