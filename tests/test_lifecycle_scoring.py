"""Lifecycle rules, frequency policy and scoring-config invariants."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from atlas import config
from atlas.classify.source_classifier import classify_by_rules, drift_check, provider_domain_match
from atlas.lifecycle.status import compute_status
from atlas.schedule.frequency import next_crawl
from atlas.verify.confidence import core_name

TODAY = date(2026, 11, 13)


def rec(**kw):
    base = dict(closing_date=None, is_rolling=None, page_status_text=None, verification_status="VERIFIED",
                confidence=97.0, consecutive_misses=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_weights_sum_to_100():
    assert sum(config.scoring()["weights"].values()) == 100


def test_status_rules():
    assert compute_status(rec(closing_date=TODAY + timedelta(days=40)), "LIVE", TODAY)[0] == "ACTIVE"
    assert compute_status(rec(closing_date=TODAY + timedelta(days=5)), "LIVE", TODAY)[0] == "EXPIRING_SOON"
    assert compute_status(rec(closing_date=TODAY - timedelta(days=1)), "LIVE", TODAY)[0] == "EXPIRED"
    assert compute_status(rec(closing_date=TODAY + timedelta(days=40), verification_status="REVIEW_REQUIRED",
                              confidence=80), "LIVE", TODAY)[0] == "REVIEW_REQUIRED"
    # one miss is not enough to declare it gone (government sites have outages)
    assert compute_status(rec(closing_date=TODAY + timedelta(days=40), consecutive_misses=1), "GONE", TODAY)[0] != \
        "NO_LONGER_VERIFIABLE"
    assert compute_status(rec(consecutive_misses=2), "GONE", TODAY)[0] == "NO_LONGER_VERIFIABLE"
    assert compute_status(rec(), "DRIFTED", TODAY)[0] == "NO_LONGER_VERIFIABLE"
    assert compute_status(rec(page_status_text="DISCONTINUED"), "LIVE", TODAY)[0] == "REVIEW_REQUIRED"


def test_frequency_deadline_overrides():
    now = datetime(2026, 11, 13, 10, 0)
    near = next_crawl("SCHOLARSHIP", source_type="GOV_CENTRAL", closing=date(2026, 11, 15), now=now)
    assert near - now == timedelta(hours=12)
    far = next_crawl("SCHOLARSHIP", source_type="GOV_CENTRAL", closing=date(2027, 3, 1), now=now)
    assert far - now >= timedelta(hours=12)
    failed = next_crawl("FAILED", source_type="GOV_CENTRAL", failures=1, now=now)
    assert failed - now == timedelta(hours=1)


def test_classification_rules():
    assert classify_by_rules("https://scholarships.gov.in/x").source_type == "GOV_CENTRAL"
    assert classify_by_rules("https://mahadbt.maharashtra.gov.in/").source_type == "GOV_STATE"
    assert classify_by_rules("https://www.iitb.ac.in/").source_type == "UNIVERSITY"
    assert classify_by_rules("https://www.buddy4study.com/x").source_type == "AGGREGATOR"
    assert not classify_by_rules("https://randomblog.com/x").is_official
    # observed 2026-11-13: lapsed domain redirecting to an unrelated site
    assert drift_check("wbmdfcscholarship.org", [], "https://www.suncity-dubai.com/payment-plan/")
    assert not drift_check("aicte.gov.in", ["aicte-india.org"], "https://www.aicte-india.org/x")
    assert provider_domain_match("Inlaks Shivdasani Foundation", "https://inlaksfoundation.org/") >= 85


def test_core_name():
    assert core_name("AICTE - Pragati Scholarship Scheme For Girl Students ( Technical Degree) (Merit Based Scheme)") \
        == "pragati scholarship scheme for girl students"
