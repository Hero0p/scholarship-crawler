"""Extractor unit tests on small synthetic fixtures (test-only data, never written to the real DB)."""
from datetime import date

from atlas.classify.page_classifier import is_soft_404, link_score
from atlas.extract.cards import extract_cards
from atlas.extract.content import parse_html
from atlas.extract.fields import find_dates, parse_inr
from atlas.extract.record import build_record
from atlas.extract.sections import segment

DETAIL = b"""<html><head><title>Example Girls Merit Scholarship | Example Foundation</title></head><body>
<nav><a href="/">Home</a><a href="/sc">Post Matric Scholarship for SC Students</a></nav>
<h1>Example Girls Merit Scholarship 2026-27</h1>
<p>The Example Foundation supports meritorious girl students from low-income families.</p>
<h2>Eligibility</h2>
<ul><li>Girl students enrolled in the first year of an undergraduate degree course.</li>
<li>Annual family income should not exceed Rs. 2.5 lakh.</li>
<li>Minimum 60% marks in Class 12.</li></ul>
<h2>Benefits</h2><p>Selected students receive Rs. 30,000 per annum till graduation.</p>
<h2>Important Dates</h2><table><tr><td>Last date to apply</td><td>15th October 2026</td></tr>
<tr><td>Institute verification till</td><td>30-11-2026</td></tr></table>
<h2>Documents Required</h2><ul><li>Aadhaar card</li><li>Income certificate</li><li>Class 12 mark sheet</li></ul>
<p><a href="https://example.org/apply">Apply Online</a></p>
<footer>Copyright 2026 Example Foundation</footer></body></html>"""


def _record(raw=DETAIL, url="https://example.org/scholarship"):
    doc = parse_html(raw, url)
    secs = segment(doc)
    d = build_record(doc, secs, official_domain="example.org", registry_provider="Example Foundation",
                     source_name="Example", domestic=True)
    return doc, secs, d


def test_parse_inr():
    assert parse_inr("30,000", None) == 30000
    assert parse_inr("2.5", "lakh") == 250000
    assert parse_inr("1,00,000", "/-") == 100000


def test_sections_detected():
    _, secs, _ = _record()
    assert {"eligibility", "benefits", "dates", "documents"} <= set(secs)


def test_fields_and_not_specified():
    doc, _, d = _record()
    assert d.rejected_reason is None
    assert d.value("name") == "Example Girls Merit Scholarship 2026-27"
    assert d.value("amount")["min"] == 30000 and d.value("amount")["period"] == "YEAR"
    assert d.value("income_limit") == 250000
    assert d.value("min_percentage") == 60.0
    assert d.value("gender") == "FEMALE"
    assert "UG" in d.value("education_levels")
    assert d.value("closing_date") == "2026-10-15"          # verification date must NOT be taken
    assert d.value("academic_year") == "2026-27"
    assert d.value("application_url")["url"] == "https://example.org/apply"
    # not on the page -> not extracted ("Not specified"), never guessed
    assert d.value("age") is None
    assert d.value("domicile_states") is None
    # the SC link lives in the <nav> (boilerplate) and must not leak into categories
    assert d.value("categories") is None


def test_every_evidence_is_verbatim():
    doc, _, d = _record()
    for name, f in d.fields.items():
        if f.kind == "TEXT":
            assert doc.text[f.start:f.end] == f.evidence, name


def test_dates_formats():
    t = "Last date: 31/08/2026. Opens on August 1, 2026. Closing date 15-Sep-2026"
    got = [h.d for h in find_dates(t)]
    assert date(2026, 8, 31) in got and date(2026, 8, 1) in got and date(2026, 9, 15) in got


CARDS = b"""<html><body><h2>Schemes On NSP</h2>
<div class="card"><h5>Alpha Merit Scholarship Scheme For Girl Students (Merit Based Scheme)</h5>
<p>Scheme Open from : 01-06-2026</p><p>Student Application Open till : 30-11-2026</p>
<p>Institute Verification Open till:15-12-2026</p><a href="/public/schemeGuidelines/alpha.pdf">Specifications</a> <a href="/faq/alpha.pdf">FAQ</a></div>
<div class="card"><h5>Beta Post Matric Scholarship For Minorities (Welfare Based Scheme)</h5>
<p>Scheme Open from : 15-06-2026</p><p>Student Application Open till : 31-10-2026</p>
<a href="/public/schemeGuidelines/beta.pdf">Specifications</a></div></body></html>"""


def test_cards():
    url = "https://scholarships.gov.in/All-Scholarships"
    doc = parse_html(CARDS, url)
    cards = extract_cards(CARDS, url, doc)
    assert len(cards) == 2
    (a, da), (b, db) = cards
    assert a.value("closing_date") == "2026-11-30" and a.value("opening_date") == "2026-06-01"
    assert b.value("closing_date") == "2026-10-31"
    assert da.endswith("/public/schemeGuidelines/alpha.pdf")
    for d in (a, b):
        for f in d.fields.values():
            assert doc.text[f.start:f.end] == f.evidence


def test_soft_404():
    doc = parse_html(b"<html><head><title>Page Not Found</title></head><body><p>Oops</p></body></html>",
                     "https://x.gov.in/a/b")
    assert is_soft_404(doc, "https://x.gov.in/a/b", "https://x.gov.in/a/b")[0]
    doc2 = parse_html(DETAIL, "https://example.org/scholarship")
    assert not is_soft_404(doc2, "https://example.org/scholarship", "https://example.org/scholarship")[0]
    assert is_soft_404(doc2, "https://ugc.gov.in/ugc_schemes", "https://ugc.gov.in/ErrorPage/index?code=404")[0]


def test_link_score():
    assert link_score("Post Matric Scholarship", "https://x.gov.in/schemes/pms") >= 3
    assert link_score("Tender notice", "https://x.gov.in/tenders") < 0
    assert link_score("Guidelines", "https://x.gov.in/public/schemeGuidelines/a.pdf") >= 2
