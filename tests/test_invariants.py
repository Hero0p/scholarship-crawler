"""Invariants checked against the REAL database (skipped when data/atlas.db does not exist).

These are the anti-hallucination guarantees: every value is backed by a verbatim snippet of a stored
official snapshot, and the snippet actually supports the value."""
import json
import sqlite3
from datetime import date

import pytest

from atlas import config, snapshots
from atlas.extract.fields import AMOUNT_RE, AMOUNT_RE2, _levels_in, find_dates, parse_inr

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="no crawled database yet")


@pytest.fixture(scope="module")
def db():
    c = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True)
    yield c
    c.close()


def _rows(db, fields=None):
    sql = ("SELECT scholarship_id, field_name, value_json, evidence_text, evidence_kind, snapshot_sha256, locator "
           "FROM field_evidence WHERE is_current = 1")
    for r in db.execute(sql):
        if fields is None or r[1] in fields:
            yield r


def test_every_text_evidence_is_verbatim_at_its_offsets(db):
    cache = {}
    for sid, f, v, ev, kind, sha, loc in _rows(db):
        if kind != "TEXT":
            continue
        if sha not in cache:
            cache[sha] = snapshots.load_text(sha)
        text = cache[sha]
        assert text is not None, f"missing text snapshot for #{sid}.{f}"
        s, e = json.loads(loc)["char"]
        assert text[s:e] == ev, f"#{sid}.{f}: evidence not at recorded offsets"


def test_no_value_without_evidence(db):
    cols = {"amount_min_inr": "amount", "income_limit_inr": "income_limit", "closing_date": "closing_date",
            "gender": "gender", "education_levels": "education_levels", "domicile_states": "domicile_states",
            "provider": "provider", "application_url": "application_url"}
    have = {(sid, f) for sid, f, *_ in _rows(db)}
    for col, field in cols.items():
        for (sid,) in db.execute(f"SELECT id FROM scholarships WHERE {col} IS NOT NULL"):
            assert (sid, field) in have, f"#{sid}: {col} has a value but no current evidence"


def test_dates_supported_by_snippet(db):
    for sid, f, v, ev, *_ in _rows(db, {"closing_date", "opening_date"}):
        d = date.fromisoformat(json.loads(v))
        assert d in {h.d for h in find_dates(ev)}, f"#{sid}.{f}={d} not in evidence {ev[:80]!r}"


def test_amounts_supported_by_snippet(db):
    for sid, f, v, ev, *_ in _rows(db, {"amount"}):
        val = json.loads(v)
        found = {parse_inr(m.group(1), m.group(2)) for rx in (AMOUNT_RE, AMOUNT_RE2) for m in rx.finditer(ev)}
        assert val["min"] in found and val["max"] in found, f"#{sid} amount {val} not in {ev[:80]!r}"


def test_education_levels_supported_by_snippet(db):
    for sid, f, v, ev, *_ in _rows(db, {"education_levels"}):
        assert set(json.loads(v)) <= set(_levels_in(ev)), f"#{sid} levels {v} not supported by {ev[:80]!r}"


def test_verified_records_pass_all_gates(db):
    for sid, conf, bd in db.execute("SELECT id, confidence, confidence_breakdown FROM scholarships "
                                    "WHERE verification_status = 'VERIFIED'"):
        b = json.loads(bd)
        assert conf >= 95 and all(g["passed"] for g in b["gates"]), f"#{sid} VERIFIED without passing gates"


def test_no_aggregator_is_an_official_source(db):
    agg = set(config.domains()["aggregators"])
    for sid, url in db.execute("SELECT id, official_source_url FROM scholarships"):
        assert not any(a in url for a in agg), f"#{sid} official source is an aggregator: {url}"
