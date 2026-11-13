"""Processing stage: snapshot -> text -> page classification -> record extraction -> upsert with
field-level change detection (PROJECT_PLAN.md §9, §10, §13).

Multi-source model: every field value is owned by the page it was extracted from (FieldEvidence.page_id).
Re-processing a page only re-evaluates (and diffs) the fields that page owns; scholarship columns are
always re-derived from the current evidence rows, so the DB can never hold a value without evidence.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from atlas import snapshots
from atlas.classify.page_classifier import classify_page
from atlas.classify.source_classifier import drift_check
from atlas.db.models import (FieldChange, FieldEvidence, Page, RunEvent, Scholarship, ScholarshipSource,
                             ScholarshipVersion, Snapshot, Source, utcnow)
from atlas.db.session import get_session
from atlas.extract.cards import extract_cards
from atlas.extract.content import Document, parse
from atlas.extract.evidence import verify_trace
from atlas.extract.fields import Extracted
from atlas.extract.record import RecordDraft, build_record
from atlas.extract.sections import segment
from atlas.schedule.frequency import next_crawl
from atlas.urls import canonicalize, registered_domain

log = logging.getLogger("atlas.process")

DISPLAY_FIELDS = ["name", "provider", "amount", "eligibility_text", "education_levels", "min_percentage",
                  "min_cgpa", "income_limit", "age", "gender", "categories", "disability_min_percent",
                  "domicile_states", "institution_text", "opening_date", "closing_date", "is_rolling",
                  "academic_year", "documents_required", "selection_process", "renewal_text",
                  "application_url", "page_status", "india_eligibility"]
# fields a detail document (guidelines PDF) may contribute to a card record
ENRICH_FIELDS = ["amount", "eligibility_text", "education_levels", "min_percentage", "min_cgpa", "income_limit",
                 "age", "gender", "categories", "disability_min_percent", "domicile_states", "institution_text",
                 "documents_required", "selection_process", "renewal_text", "provider"]


def norm_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\b20\d{2}\s*[-–/]\s*\d{2,4}\b|\b20\d{2}\b", " ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


DISCRIMINATORS = {"pre", "post", "ug", "pg", "degree", "diploma", "junior", "senior", "jrf", "srf", "sc", "st",
                  "obc", "ebc", "dnt", "ews", "girls", "girl", "boys", "minority", "minorities", "disabilities",
                  "disabled", "phd", "masters", "bachelors", "undergraduate", "postgraduate", "school", "college",
                  "fresh", "renewal", "i", "ii", "iii", "part", "level", "class", "matric", "nts", "pgs", "technical"}
STOP = {"the", "of", "for", "and", "to", "in", "a", "an", "scheme", "scholarship", "scholarships", "programme",
        "program", "based", "merit", "welfare"}


def same_scheme(a: str, b: str) -> bool:
    """Conservative identity test: typos/punctuation may differ, discriminating words may not.
    ('Pre Matric ...' vs 'Post Matric ...', 'NTS-UG' vs 'NTS-PG', 'Degree' vs 'Diploma' are different schemes.)"""
    na, nb = norm_name(a), norm_name(b)
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    diff = ta ^ tb
    if diff & DISCRIMINATORS or any(t.isdigit() for t in diff):
        return False
    if not (diff - STOP):
        return True
    return fuzz.ratio(na, nb) >= 97


def norm_value(field: str, v):
    if v is None:
        return None
    if field == "amount" and isinstance(v, dict):
        return {"min": v.get("min"), "max": v.get("max"), "period": v.get("period")}
    if field == "application_url" and isinstance(v, dict):
        return v.get("url")
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    if isinstance(v, str):
        return re.sub(r"\s+", " ", v).strip()
    return v


def _dumps(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


# ----------------------------------------------------------------------------- evidence -> columns

def current_values(s: Session, sch_id: int, page_id: int | None = None) -> dict:
    """field -> raw value from current evidence rows (optionally only those owned by one page)."""
    q = s.query(FieldEvidence).filter_by(scholarship_id=sch_id, is_current=True)
    if page_id is not None:
        q = q.filter(FieldEvidence.page_id == page_id)
    vals: dict = {}
    for e in q.order_by(FieldEvidence.id):
        if e.field_name == "official_source_url":
            continue
        v = json.loads(e.value_json)
        if e.field_name == "categories":
            vals.setdefault("categories", []).append(v)
        else:
            vals[e.field_name] = v
    return vals


def refresh_columns(s: Session, sch: Scholarship) -> None:
    v = current_values(s, sch.id)
    g = v.get
    if g("name"):
        sch.name = g("name")
    sch.provider = g("provider")
    a = g("amount") or {}
    sch.amount_text = a.get("text")
    sch.amount_min_inr, sch.amount_max_inr, sch.amount_period = a.get("min"), a.get("max"), a.get("period")
    sch.eligibility_text = g("eligibility_text")
    sch.education_levels = _dumps(g("education_levels")) if g("education_levels") else None
    sch.min_percentage, sch.min_cgpa = g("min_percentage"), g("min_cgpa")
    sch.income_limit_inr = g("income_limit")
    inc = s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True, field_name="income_limit").first()
    sch.income_text = inc.evidence_text if inc else None
    age = g("age") or {}
    sch.age_min, sch.age_max = age.get("min"), age.get("max")
    sch.gender = g("gender")
    sch.categories = _dumps(sorted(set(g("categories")))) if g("categories") else None
    sch.disability_min_percent = g("disability_min_percent")
    sch.domicile_states = _dumps(g("domicile_states")) if g("domicile_states") else None
    sch.institution_text = g("institution_text")
    sch.closing_date = date.fromisoformat(g("closing_date")) if g("closing_date") else None
    cd = s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True, field_name="closing_date").first()
    sch.deadline_text = cd.evidence_text if cd else None
    sch.opening_date = date.fromisoformat(g("opening_date")) if g("opening_date") else None
    sch.is_rolling = True if g("is_rolling") else None
    sch.academic_year = g("academic_year")
    sch.documents_required = g("documents_required")
    sch.selection_process = g("selection_process")
    sch.renewal_text = g("renewal_text")
    app = g("application_url") or {}
    sch.application_url, sch.application_mode = app.get("url"), app.get("mode")
    sch.page_status_text = g("page_status")
    sch.field_values_json = _dumps({k: norm_value(k, val) for k, val in v.items()})


def _locator(doc: Document, f: Extracted) -> str:
    if f.start < 0:
        return "{}"
    loc = {"char": [f.start, f.end]}
    b = doc.block_at(f.start)
    if b and b.page:
        loc["pdf_page"] = b.page
    return json.dumps(loc)


def write_page_evidence(s: Session, sch: Scholarship, d: RecordDraft, doc: Document, page: Page, sha: str,
                        run_id, raw: bytes, only: set[str] | None = None, url_row: bool = True) -> dict[str, FieldEvidence]:
    """Replace the evidence rows owned by `page` with the draft's fields."""
    s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True, page_id=page.id) \
        .update({"is_current": False})
    src_url = page.final_url or page.url
    out: dict[str, FieldEvidence] = {}

    def add(name: str, f: Extracted, value) -> FieldEvidence:
        ev = FieldEvidence(scholarship_id=sch.id, field_name=name, value_json=json.dumps(value, default=str),
                           evidence_text=f.evidence, evidence_kind=f.kind, source_url=src_url, page_id=page.id,
                           snapshot_sha256=sha, locator=_locator(doc, f), section=f.section, extractor=f.extractor,
                           extraction_pass=f.pass_, run_id=run_id, is_current=True)
        ev.trace_ok = verify_trace(ev, doc.text, raw)
        s.add(ev)
        return ev

    for name, f in d.fields.items():
        if name not in DISPLAY_FIELDS or (only is not None and name not in only):
            continue
        if name == "categories" and f.extra.get("all"):
            evs = [add(name, c, c.value) for c in f.extra["all"]]
            out[name] = evs[0]
        else:
            out[name] = add(name, f, f.value)
    if url_row:
        ev = FieldEvidence(scholarship_id=sch.id, field_name="official_source_url", value_json=json.dumps(src_url),
                           evidence_text=f"HTTP {page.http_status} from {src_url} on "
                                         f"{(page.last_ok_at or utcnow()).date()} (snapshot {sha[:12]})",
                           evidence_kind="URL", source_url=src_url, page_id=page.id, snapshot_sha256=sha,
                           locator="{}", extractor="crawler", extraction_pass="META", run_id=run_id,
                           is_current=True, trace_ok=True)
        s.add(ev)
        out["official_source_url"] = ev
    s.flush()
    return out


def record_json(sch: Scholarship) -> str:
    return _dumps({c.name: getattr(sch, c.name) for c in Scholarship.__table__.columns})


def prune_untraceable(d: RecordDraft, doc: Document, raw: bytes) -> list[str]:
    """Anti-hallucination guard: drop any field whose evidence is not verbatim in the snapshot."""
    dropped = []
    for name, f in list(d.fields.items()):
        items = f.extra.get("all") if name == "categories" and f.extra.get("all") else [f]
        keep = []
        for it in items:
            probe = FieldEvidence(field_name=name, value_json=json.dumps(it.value, default=str), evidence_text=it.evidence,
                                  evidence_kind=it.kind,
                                  locator=json.dumps({"char": [it.start, it.end]}) if it.start >= 0 else "{}")
            if verify_trace(probe, doc.text, raw):
                keep.append(it)
        if not keep:
            dropped.append(name)
            del d.fields[name]
            d.consistency.pop(name, None)
        elif name == "categories" and len(keep) != len(items):
            f.extra["all"] = keep
            f.value = [k.value for k in keep]
    if dropped:
        log.warning("dropped untraceable fields %s on %s", dropped, doc.url)
    return dropped


# ----------------------------------------------------------------------------- upsert

def find_existing(s: Session, page: Page, src: Source, name: str, card: bool) -> tuple[Scholarship | None, str | None]:
    """(scholarship, role of `page` for it)."""
    fp = f"{norm_name(name)}@{src.registered_domain}"
    if not card:
        link = s.query(ScholarshipSource).filter_by(page_id=page.id, role="PRIMARY").first()
        if link:
            sch = s.get(Scholarship, link.scholarship_id)
            if sch and sch.record_kind != "CARD":
                return sch, "PRIMARY"
    sch = s.query(Scholarship).filter_by(fingerprint=fp).one_or_none()
    if sch is None:
        for cand in s.query(Scholarship).filter(Scholarship.fingerprint.like(f"%@{src.registered_domain}")):
            if same_scheme(cand.name, name):
                sch = cand
                break
    if sch is None:
        return None, None
    link = s.query(ScholarshipSource).filter_by(scholarship_id=sch.id, page_id=page.id).first()
    return sch, (link.role if link else None)


def _diff_and_log(s: Session, sch: Scholarship, page: Page, old: dict, new_ev: dict, old_ev: dict, run_id,
                  fields: set[str]) -> list[str]:
    new = current_values(s, sch.id, page.id)
    changed = []
    for k in sorted(fields):
        o, n = norm_value(k, old.get(k)), norm_value(k, new.get(k))
        if _dumps(o) == _dumps(n):
            continue
        changed.append(k)
        s.add(FieldChange(scholarship_id=sch.id, field_name=k, old_value_json=_dumps(o), new_value_json=_dumps(n),
                          change_kind="ADDED" if o is None else "REMOVED" if n is None else "UPDATED",
                          detected_at=utcnow(), run_id=run_id, source_url=page.final_url or page.url,
                          old_source_url=old_ev[k].source_url if k in old_ev else None,
                          old_evidence_id=old_ev[k].id if k in old_ev else None,
                          new_evidence_id=new_ev[k].id if k in new_ev else None,
                          old_evidence_text=old_ev[k].evidence_text if k in old_ev else None,
                          new_evidence_text=new_ev[k].evidence_text if k in new_ev else None, origin="LIVE"))
    return changed


def upsert(s: Session, page: Page, src: Source, doc: Document, d: RecordDraft, sha: str, raw: bytes, run_id,
           card: bool = False, detail_url: str | None = None) -> str:
    name = str(d.value("name"))
    sch, role = find_existing(s, page, src, name, card)
    now = utcnow()
    src_url = page.final_url or page.url
    if sch is None:
        sch = Scholarship(fingerprint=f"{norm_name(name)}@{src.registered_domain}", name=name,
                          official_source_url=src_url, official_source_page_id=page.id,
                          official_source_tier=src.trust_tier, source_id=src.id, source_type=src.source_type,
                          discovered_via=src.origin, first_seen_at=now, version=1, funding_type=d.funding_type,
                          india_eligible=d.india_eligible, record_kind="CARD" if card else "PAGE",
                          detail_doc_url=detail_url, last_checked_at=now)
        keyed = list(d.consistency)
        sch.consistency_ratio = (sum(d.consistency[k] for k in keyed) / len(keyed)) if keyed else None
        s.add(sch)
        s.flush()
        s.add(ScholarshipSource(scholarship_id=sch.id, page_id=page.id, url=src_url,
                                registered_domain=src.registered_domain, role="PRIMARY", is_official=src.is_official))
        write_page_evidence(s, sch, d, doc, page, sha, run_id, raw)
        refresh_columns(s, sch)
        s.add(ScholarshipVersion(scholarship_id=sch.id, version=1, record_json=record_json(sch), run_id=run_id))
        s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="NEW", detail_json=json.dumps({"url": src_url})))
        return "NEW"

    if role != "PRIMARY":
        if role is None:
            s.add(ScholarshipSource(scholarship_id=sch.id, page_id=page.id, url=src_url,
                                    registered_domain=src.registered_domain, role="CORROBORATING",
                                    is_official=src.is_official))
        else:
            s.query(ScholarshipSource).filter_by(scholarship_id=sch.id, page_id=page.id).update({"last_seen_at": now})
        return "CORROBORATING"

    old = current_values(s, sch.id, page.id)
    old_ev = {e.field_name: e for e in s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True,
                                                                         page_id=page.id)}
    new_ev = write_page_evidence(s, sch, d, doc, page, sha, run_id, raw)
    fields = (set(old) | set(d.fields)) & set(DISPLAY_FIELDS)
    changed = _diff_and_log(s, sch, page, old, new_ev, old_ev, run_id, fields)
    keyed = list(d.consistency)
    sch.consistency_ratio = (sum(d.consistency[k] for k in keyed) / len(keyed)) if keyed else sch.consistency_ratio
    if detail_url:
        sch.detail_doc_url = detail_url
    sch.official_source_url = src_url
    sch.last_checked_at = now
    refresh_columns(s, sch)
    s.query(ScholarshipSource).filter_by(scholarship_id=sch.id, page_id=page.id).update({"last_seen_at": now})
    if not changed:
        s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="UNCHANGED",
                       detail_json=json.dumps({"reason": "page changed, extracted values identical"})))
        return "UNCHANGED"
    sch.version += 1
    sch.last_changed_at = now
    s.add(ScholarshipVersion(scholarship_id=sch.id, version=sch.version, record_json=record_json(sch), run_id=run_id))
    s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="CHANGED", detail_json=json.dumps({"fields": changed})))
    return "CHANGED"


# ----------------------------------------------------------------------------- enrichment from detail docs

PROVIDER_IN_DOC = re.compile(r"^\s*(?:Government of India\s*[,:-]?\s*)?(Ministry of [A-Z][A-Za-z,&' ]+|"
                             r"Department of [A-Z][A-Za-z,&' ]+|All India Council for Technical Education|"
                             r"University Grants Commission)\s*$")


def enrich_card_records(run_id=None) -> dict:
    """Card records get eligibility/amount/etc. from their official detail document (same domain)."""
    stats = {"enriched": 0, "changed": 0, "missing_doc": 0, "shared_doc_skipped": 0}
    with get_session() as s:
        recs = s.query(Scholarship).filter(Scholarship.detail_doc_url.isnot(None)).all()
        usage: dict[str, int] = {}
        for r in recs:
            usage[canonicalize(r.detail_doc_url)] = usage.get(canonicalize(r.detail_doc_url), 0) + 1
        for sch in recs:
            page = s.query(Page).filter_by(canonical_url=canonicalize(sch.detail_doc_url)).first()
            if not page or not page.latest_snapshot_sha:
                stats["missing_doc"] += 1
                continue
            if usage[canonicalize(sch.detail_doc_url)] > 1:
                # one document describes several schemes (e.g. pre- AND post-matric): its amounts/eligibility
                # cannot be attributed to a single scheme without guessing -> link it, never copy values.
                n = s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True, page_id=page.id) \
                    .update({"is_current": False})
                if n:
                    refresh_columns(s, sch)
                stats["shared_doc_skipped"] += 1
                continue
            src = s.get(Source, page.source_id)
            raw = snapshots.load_raw(page.latest_snapshot_sha)
            if raw is None or src is None:
                continue
            try:
                doc, sections, _ = analyse(raw, page.final_url or page.url, page.content_type, page.url)
            except Exception:  # noqa: BLE001
                continue
            snapshots.store_text(page.latest_snapshot_sha, doc.text)
            d = build_record(doc, sections, official_domain=src.registered_domain, registry_provider=None,
                             source_name=src.name, domestic=True, require_name=False)
            # provider: only when the card has none or only a registry provider
            prov_row = s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True,
                                                        field_name="provider").first()
            d.fields.pop("provider", None)
            if prov_row is None or prov_row.evidence_kind == "REGISTRY" or prov_row.page_id == page.id:
                # provider = issuing ministry/department on the document's LETTERHEAD (first lines only);
                # later mentions are often references to other ministries
                for b in doc.blocks[:8]:
                    m = PROVIDER_IN_DOC.match(b.text)
                    if m and not re.search(r"\b(Ministry|Department) of (Ministry|Department)\b", b.text) \
                            and len(b.text.split()) >= 3 and len(b.text) <= 90:
                        d.fields["provider"] = Extracted(b.text, b.text, b.start, b.end, extractor="provider:letterhead",
                                                         pass_="META")
                        break
            prune_untraceable(d, doc, raw)
            owned_elsewhere = {e.field_name for e in s.query(FieldEvidence).filter(
                FieldEvidence.scholarship_id == sch.id, FieldEvidence.is_current.is_(True),
                FieldEvidence.page_id != page.id)}
            allowed = {f for f in ENRICH_FIELDS if f not in owned_elsewhere}
            if prov_row is not None and prov_row.evidence_kind == "REGISTRY":
                allowed.add("provider")
                if "provider" in d.fields:
                    s.query(FieldEvidence).filter_by(id=prov_row.id).update({"is_current": False})
            old = current_values(s, sch.id, page.id)
            old_ev = {e.field_name: e for e in s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True,
                                                                                 page_id=page.id)}
            new_ev = write_page_evidence(s, sch, d, doc, page, page.latest_snapshot_sha, run_id, raw,
                                         only=allowed, url_row=False)
            had_before = bool(old)
            changed = _diff_and_log(s, sch, page, old, new_ev, old_ev, run_id, allowed) if had_before else []
            if not s.query(ScholarshipSource).filter_by(scholarship_id=sch.id, page_id=page.id).first():
                s.add(ScholarshipSource(scholarship_id=sch.id, page_id=page.id, url=page.final_url or page.url,
                                        registered_domain=src.registered_domain, role="DETAIL_DOC",
                                        is_official=src.is_official))
            refresh_columns(s, sch)
            stats["enriched"] += 1
            if changed:
                stats["changed"] += 1
                sch.version += 1
                sch.last_changed_at = utcnow()
                s.add(ScholarshipVersion(scholarship_id=sch.id, version=sch.version, record_json=record_json(sch),
                                         run_id=run_id))
                s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="CHANGED",
                               detail_json=json.dumps({"fields": changed, "via": "detail document"})))
        s.commit()
    return stats


# ----------------------------------------------------------------------------- page loop

def analyse(raw: bytes, url: str, content_type: str | None, requested_url: str):
    doc = parse(raw, url, content_type)
    sections = segment(doc)
    verdict = classify_page(doc, sections, requested_url, url)
    return doc, sections, verdict


def _domestic(page: Page, src: Source) -> bool:
    return registered_domain(page.final_url or page.url).endswith(".in") or \
        src.source_type in ("GOV_CENTRAL", "GOV_STATE", "REGULATOR") or \
        (src.origin == "SEED" and src.source_type in ("CORPORATE_CSR", "FOUNDATION_TRUST", "UNIVERSITY"))


def process_pages(run_id=None, reprocess: bool = False, limit: int | None = None) -> dict:
    stats = {"pages": 0, "DETAIL": 0, "LISTING": 0, "IRRELEVANT": 0, "SOFT_404": 0, "DRIFTED": 0, "CARDS": 0,
             "NEW": 0, "CHANGED": 0, "UNCHANGED": 0, "CORROBORATING": 0, "REJECTED": 0, "errors": 0}
    with get_session() as s:
        q = s.query(Page).filter(Page.latest_snapshot_sha.isnot(None))
        if not reprocess:
            q = q.filter((Page.processed_snapshot_sha.is_(None)) |
                         (Page.processed_snapshot_sha != Page.latest_snapshot_sha))
        pages = q.order_by(Page.id).all()
        if limit:
            pages = pages[:limit]
        sources = {src.id: src for src in s.query(Source)}
        for i, page in enumerate(pages, 1):
            src = sources.get(page.source_id)
            sha = page.latest_snapshot_sha
            raw = snapshots.load_raw(sha)
            if raw is None or src is None:
                continue
            stats["pages"] += 1
            try:
                doc, sections, verdict = analyse(raw, page.final_url or page.url, page.content_type, page.url)
            except Exception as e:  # noqa: BLE001 - corrupt PDFs etc.
                log.warning("parse failed %s: %s", page.url, e)
                stats["errors"] += 1
                page.page_kind = "ERROR"
                page.processed_snapshot_sha = sha
                continue
            snapshots.store_text(sha, doc.text)
            snap = s.get(Snapshot, sha)
            if snap is not None:
                snap.text_path = str(snapshots.text_path(sha))
            page.title = (doc.title or (doc.h1[0] if doc.h1 else ""))[:300]
            kind = verdict.kind
            if drift_check(src.registered_domain, json.loads(src.domain_aliases or "[]"), page.final_url or page.url):
                kind = "DRIFTED"
                if page.depth == 0:
                    src.approval_status = "BROKEN"
                    src.approval_reason = f"domain drift: {page.url} -> {page.final_url}"
            page.page_kind = kind
            page.relevance_score = verdict.score
            stats[kind] = stats.get(kind, 0) + 1
            outcomes: list[str] = []
            closing = opening = None
            extractable = src.role == "CRAWL" and src.source_type not in ("AGGREGATOR", "NEWS_BLOG")
            if extractable and kind == "DETAIL":
                d = build_record(doc, sections, official_domain=src.registered_domain,
                                 registry_provider=src.provider_name, source_name=src.name,
                                 domestic=_domestic(page, src))
                if not d.rejected_reason:
                    prune_untraceable(d, doc, raw)
                    if "name" not in d.fields:
                        d.rejected_reason = "name evidence not traceable"
                if d.rejected_reason:
                    stats["REJECTED"] += 1
                    page.page_kind = "DETAIL_REJECTED"
                    log.info("rejected %s: %s", page.url, d.rejected_reason)
                else:
                    outcomes.append(upsert(s, page, src, doc, d, sha, raw, run_id))
                    cd, od = d.fields.get("closing_date"), d.fields.get("opening_date")
                    closing = date.fromisoformat(cd.value) if cd else None
                    opening = date.fromisoformat(od.value) if od else None
            elif extractable and kind in ("LISTING", "IRRELEVANT") and doc.kind == "HTML":
                cards = extract_cards(raw, page.final_url or page.url, doc, domestic=_domestic(page, src))
                seen_here: set[str] = set()
                for d, detail in cards:
                    nn = norm_name(str(d.value("name")))
                    if nn in seen_here:          # same scheme listed twice (e.g. two cycles) on one page
                        continue
                    seen_here.add(nn)
                    if src.provider_name and "provider" not in d.fields:
                        prov = Extracted(src.provider_name, f"Source registry entry: {src.name} -> {src.provider_name}",
                                         -1, -1, kind="REGISTRY", extractor="provider:registry", pass_="META")
                        d.fields["provider"] = prov
                    prune_untraceable(d, doc, raw)
                    if "name" not in d.fields or "closing_date" not in d.fields:
                        continue
                    outcomes.append(upsert(s, page, src, doc, d, sha, raw, run_id, card=True, detail_url=detail))
                    c = date.fromisoformat(d.fields["closing_date"].value)
                    closing = c if closing is None else min(closing, c)
                if cards:
                    page.page_kind = "LISTING_CARDS"
                    stats["CARDS"] += len(cards)
            for o in outcomes:
                stats[o] += 1
            page.processed_snapshot_sha = sha
            page.next_crawl_at = next_crawl(
                "SCHOLARSHIP" if outcomes else page.page_kind, source_type=src.source_type,
                unchanged_streak=page.unchanged_streak or 0, closing=closing, opening=opening,
                changed="CHANGED" in outcomes)
            if i % 50 == 0:
                s.commit()
                log.info("processed %d/%d pages", i, len(pages))
        # fetched-but-unchanged (304 / identical bytes) and failed pages still need a next crawl time
        if run_id is not None:
            for page in s.query(Page).filter(Page.last_run_id == run_id):
                src = sources.get(page.source_id)
                st = src.source_type if src else "UNKNOWN"
                if page.consecutive_failures:
                    page.next_crawl_at = next_crawl("FAILED", source_type=st, failures=page.consecutive_failures)
                elif page.processed_snapshot_sha == page.latest_snapshot_sha and page.page_kind:
                    is_sch = s.query(ScholarshipSource).filter_by(page_id=page.id).first() is not None
                    page.next_crawl_at = next_crawl("SCHOLARSHIP" if is_sch else page.page_kind, source_type=st,
                                                    unchanged_streak=page.unchanged_streak or 0)
        s.commit()
    return stats


def touch_unchanged_scholarships(run_id) -> int:
    """Scholarship pages re-fetched this run whose snapshot did not change (304 or identical bytes)."""
    n = 0
    with get_session() as s:
        touched = {sid for (sid,) in s.query(RunEvent.scholarship_id).filter(RunEvent.run_id == run_id)}
        for sch in s.query(Scholarship).filter(Scholarship.is_active_record.is_(True)):
            page = s.get(Page, sch.official_source_page_id) if sch.official_source_page_id else None
            if not page or page.last_run_id != run_id or sch.id in touched:
                continue
            sch.last_checked_at = page.last_fetched_at
            if page.http_status in (200, 304):
                s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="UNCHANGED",
                               detail_json=json.dumps({"reason": "304 Not Modified" if page.http_status == 304
                                                       else "identical snapshot"})))
                n += 1
        s.commit()
    return n
