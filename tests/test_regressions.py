"""Regression tests for accuracy bugs found while auditing Run 1 against real pages (synthetic re-creations)."""
from atlas.extract.content import parse_html
from atlas.extract.fields import DISCONTINUED, extract_page_status, text_quality
from atlas.extract.record import build_record
from atlas.extract.sections import segment
from atlas.process import same_scheme

PORTAL = b"""<html><head><title>SchemeData</title></head><body>
<div class="notice">The last date for reverting "Right to Give Up" applications is 30th June 2025.
Fresh applications for the Academic Year 2026-27 will be accepted through the new portal.</div>
<h1>Stipend of Rs. 500 per month for trainees in Craftsman Training Scheme in Govt ITI</h1>
<h3>Department Name</h3><p>Skill Development Department</p>
<h3>Benefits</h3><ul><li>Stipend 500/- Rs per month</li></ul>
<h3>Eligibility</h3><ol><li>The trainee should be domicile in Maharashtra.</li>
<li>SC trainees with an annual income of less than Rs. 2.5 lac are also eligible for the Post Matric Scholarship.</li>
<li>Students enrolled in a two-year trade.</li></ol></body></html>"""


def _rec(raw, url="https://mahadbt.maharashtra.gov.in/SchemeData?x=1"):
    doc = parse_html(raw, url)
    return build_record(doc, segment(doc), official_domain="maharashtra.gov.in",
                        registry_provider="Government of Maharashtra", source_name="MahaDBT", domestic=True)


def test_site_banner_dates_are_not_scheme_deadlines():
    d = _rec(PORTAL)
    assert d.value("closing_date") is None            # banner "last date ... 30th June 2025" is not this scheme's
    assert d.value("academic_year") is None           # banner year is not this scheme's cycle


def test_cross_reference_is_not_a_category_criterion():
    assert _rec(PORTAL).value("categories") is None   # "SC trainees ... also eligible for <other scheme>"


def test_amount_after_number_and_provider_label():
    d = _rec(PORTAL)
    assert d.value("amount")["min"] == 500 and d.value("amount")["period"] == "MONTH"
    assert d.value("provider") == "Skill Development Department"
    assert d.value("domicile_states") == ["Maharashtra"]


def test_distinct_schemes_never_merge():
    assert not same_scheme("Pre Matric Scholarship For Students With Disabilities",
                           "Post Matric Scholarship For Students With Disabilities")
    assert not same_scheme("Icar National Talent Scholarship(Nts-Ug)", "Icar National Talent Scholarship(Nts-Pg)")
    assert not same_scheme("Swanath Scholarship Scheme (Technical Degree)", "Swanath Scholarship Scheme (Technical Diploma)")
    assert same_scheme("Kotak Kanya Scholarship 2026-27", "Kotak Kanya Scholarship 2025-26")


def test_conditional_discontinuation_is_not_a_withdrawn_scheme():
    assert not DISCONTINUED.search("the scholarship shall be discontinued if the student fails")
    assert DISCONTINUED.search("The scheme has been discontinued from 2025-26.")
    doc = parse_html(b"<html><body><h1>X Scholarship</h1><p>The scholarship shall be discontinued if the "
                     b"student gets any other assistance.</p></body></html>", "https://x.gov.in/a")
    assert extract_page_status(doc) is None


GUIDE = b"""<html><body><h1>Example National Scholarship for Post Graduate Studies</h1>
<h2>Eligibility</h2><ol>
<li>Half of the scholarships are reserved for female candidates.</li>
<li>Note: A 'single girl child' means the only girl child in the family.</li>
<li>Institutions having more than 80% admission consecutively for last 5 years.</li>
<li>Candidates should be domicile of Union Territories of J&amp;K and Ladakh.</li>
<li>Candidates who want admission under Lateral Entry have to pass Diploma in Engineering.</li>
<li>Students pursuing a postgraduate degree in a recognised university with minimum 60% marks in graduation.</li>
</ol><p>Rs. 15,000 per month is the scholarship amount.</p></body></html>"""


def test_reservation_and_prerequisite_phrases_are_not_criteria():
    d = _rec(GUIDE, "https://example.gov.in/nspgs")
    assert d.value("gender") is None                      # reservation / definition, not female-only
    assert d.value("min_percentage") == 60.0              # not the 80% institutional admission figure
    assert d.value("domicile_states") == ["Jammu and Kashmir", "Ladakh"]
    assert d.value("education_levels") == ["PG"]          # not DIPLOMA (prerequisite for lateral entry)


EXCL = b"""<html><body><h1>Example Special Scholarship Scheme</h1><h2>Eligibility</h2>
<p>(i) Candidates pursuing undergraduate degree courses in recognised colleges.</p>
<p>The following are not eligible:</p>
<p>(ii) Candidates pursuing Diploma course.</p><p>(iii) Candidates pursuing Post-Graduate level studies.</p>
<p>(iv) Students belonging to SC category availing other scholarships.</p>
<p>7. Rs. 20,000 per annum is the scholarship amount.</p></body></html>"""


def test_exclusion_lists_are_not_criteria():
    d = _rec(EXCL, "https://example.gov.in/sss")
    assert d.value("education_levels") == ["UG"]
    assert d.value("categories") is None


def test_ocr_garbled_text_detected():
    assert text_quality("scheme, t00,000 fresh scholarships for l5th Finance Commission cycle 2O2l-22 to 2025-26 "
                        "awarded the students every year as per the guidelines of the ministry") < 0.995
    assert text_quality("Rs. 50,000 per annum for every year of study for a maximum of 4 years") >= 0.995
