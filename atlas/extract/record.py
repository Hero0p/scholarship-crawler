"""Assemble a scholarship record (fields + evidence) from one official page."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.extract import fields as F
from atlas.extract.content import Document
from atlas.extract.sections import Section, segment

KEY_FIELDS = ["amount", "closing_date", "income_limit", "education_levels", "gender", "categories"]


@dataclass
class RecordDraft:
    fields: dict[str, F.Extracted] = field(default_factory=dict)
    consistency: dict[str, bool] = field(default_factory=dict)   # key field -> pass A == pass B
    india_eligible: bool = True
    funding_type: str = "SCHOLARSHIP"
    rejected_reason: str | None = None

    def value(self, name: str):
        f = self.fields.get(name)
        return f.value if f else None


def _norm(v):
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    if isinstance(v, dict):
        return {k: v[k] for k in sorted(v) if k != "text"}
    return v


def _pick(name: str, a: F.Extracted | None, b: F.Extracted | None, draft: RecordDraft, allow_b: bool = True):
    if a is not None:
        draft.fields[name] = a
    elif b is not None and allow_b:
        draft.fields[name] = b
    if a is not None or b is not None:
        draft.consistency[name] = (a is not None and b is not None and _norm(a.value) == _norm(b.value))


def funding_type_of(name: str) -> str:
    n = name.lower()
    if re.search(r"\bloan\b", n) and "scholarship" not in n:
        return "LOAN"
    if "fellowship" in n:
        return "FELLOWSHIP"
    if re.search(r"\baward|prize\b", n):
        return "AWARD"
    if re.search(r"\bgrant\b", n):
        return "GRANT"
    if re.search(r"fee waiver|freeship", n):
        return "FEE_WAIVER"
    return "SCHOLARSHIP"


def build_record(doc: Document, sections: dict[str, Section], *, official_domain: str,
                 registry_provider: str | None, source_name: str | None, domestic: bool,
                 raw_html: str | None = None, require_name: bool = True) -> RecordDraft:
    d = RecordDraft()
    if doc.kind == "PDF" and F.text_quality(doc.text) < 0.995:
        d.rejected_reason = "PDF text layer looks OCR-garbled; values would be unreliable"
        return d
    name = F.extract_name(doc)
    if not name and require_name:
        d.rejected_reason = "no specific scholarship name on page"
        return d
    if name:
        d.fields["name"] = name
    if not re.search(r"\b(students?|scholars?|candidates?|applicants?|girls?|awardees?|fellows?|pursuing|"
                     r"studying|enrolled|learners?)\b", doc.text, re.I):
        d.rejected_reason = "page is not addressed to students/candidates"
        return d
    d.funding_type = funding_type_of(str(name.value)) if name else "SCHOLARSHIP"
    # Content scope: when the scheme name is the page heading, everything above it is site chrome
    # (banners, notices, tickers) and must not feed any field (e.g. a portal-wide "last date" notice).
    lo = name.start if name is not None and name.extractor == "name:h1" else 0
    if lo > 0:
        sections = segment(doc, min_start=lo)
    if d.funding_type == "LOAN":
        d.rejected_reason = "education loan, not a scholarship"
        return d

    prov = F.extract_provider(doc, registry_provider, source_name, lo)
    if prov:
        d.fields["provider"] = prov

    ok, ev = F.india_eligible(doc, domestic)
    d.india_eligible = ok
    if ev is not None:
        d.fields["india_eligibility"] = ev
    if not ok:
        d.rejected_reason = "no evidence that Indian students are eligible"
        return d

    amt_a, amt_b = F.extract_amount(doc, sections, lo)
    _pick("amount", amt_a, amt_b, d)

    elig = F.section_text_field(doc, sections, "eligibility", "eligibility_text", 1500)
    if elig:
        d.fields["eligibility_text"] = elig
    scopes_a, scopes_b = F.eligibility_scopes(doc, sections, lo)
    has_elig_section = bool(scopes_a)

    def ab(fn, *args):
        a = fn(doc, scopes_a, *args) if scopes_a else None
        b = fn(doc, scopes_b, *args)
        return a, b

    # Structured criteria: full-text fallback is only allowed when the page has no eligibility section.
    inc_a, inc_b = ab(F.extract_income)
    _pick("income_limit", inc_a, inc_b, d, allow_b=not has_elig_section)
    age_a, age_b = ab(F.extract_age)
    _pick("age", age_a, age_b, d, allow_b=not has_elig_section)
    g_a, g_b = ab(F.extract_gender, name)
    _pick("gender", g_a, g_b, d)   # gender is often stated in the intro ("supports girls ...")
    cats_a = F.extract_categories(doc, scopes_a, name) if scopes_a else F.extract_categories(doc, [], name)
    cats_b = F.extract_categories(doc, scopes_b, None)
    if cats_a or (cats_b and not has_elig_section):
        chosen = cats_a or cats_b
        first = chosen[0]
        d.fields["categories"] = F.Extracted(value=[c.value for c in chosen], evidence=first.evidence,
                                             start=first.start, end=first.end, section=first.section,
                                             extractor="category", pass_=first.pass_, extra={"all": list(chosen)})
        if cats_a and cats_b:
            d.consistency["categories"] = sorted(str(c.value).split(" ")[0] for c in cats_a) == \
                sorted(str(c.value).split(" ")[0] for c in cats_b)
    dis_a, dis_b = ab(F.extract_disability)
    _pick("disability_min_percent", dis_a, dis_b, d, allow_b=not has_elig_section)
    (pct_a, cg_a), (pct_b, cg_b) = (F.extract_academic(doc, scopes_a) if scopes_a else (None, None)), F.extract_academic(doc, scopes_b)
    _pick("min_percentage", pct_a, pct_b, d, allow_b=not has_elig_section)
    _pick("min_cgpa", cg_a, cg_b, d, allow_b=not has_elig_section)
    dom_a, dom_b = ab(F.extract_domicile)
    _pick("domicile_states", dom_a, dom_b, d, allow_b=not has_elig_section)
    edu_a, edu_b = F.extract_education(doc, sections, name)
    _pick("education_levels", edu_a, edu_b, d)
    inst = F.extract_institution(doc, sections)
    if inst:
        d.fields["institution_text"] = inst

    c_a, c_b, opening = F.extract_dates(doc, sections, lo)
    _pick("closing_date", c_a, c_b, d)
    if opening:
        d.fields["opening_date"] = opening
    roll = F.extract_rolling(doc, lo)
    if roll:
        d.fields["is_rolling"] = roll
    ay = F.extract_academic_year(doc, str(name.value) if name else None, lo)
    if ay:
        d.fields["academic_year"] = ay
    st = F.extract_page_status(doc, lo)
    if st:
        d.fields["page_status"] = st

    docs = F.extract_documents(doc, sections)
    if docs:
        d.fields["documents_required"] = docs
    sel = F.section_text_field(doc, sections, "selection", "selection_process", 1000)
    if sel:
        d.fields["selection_process"] = sel
    ren = F.section_text_field(doc, sections, "renewal", "renewal_text", 800)
    if ren:
        d.fields["renewal_text"] = ren
    app = F.extract_application(doc, official_domain, raw_html)
    if app:
        d.fields["application_url"] = app

    # F4 validity: benefit or eligibility must be evidenced
    if not any(k in d.fields for k in ("amount", "eligibility_text", "education_levels", "income_limit")):
        d.rejected_reason = "neither benefit nor eligibility evidenced"
    return d
