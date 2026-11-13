"""Atlas command-line interface.

    python -m atlas.cli init                 # create DB + load seed registry
    python -m atlas.cli run [--force]        # full pipeline (recheck -> leads -> crawl -> process -> verify -> lifecycle)
    python -m atlas.cli stats                # summary
    python -m atlas.cli wayback --limit 20   # historical baselines from the Internet Archive
    python -m atlas.cli export / audit       # sample data / manual-audit sheet
    python -m atlas.cli schedule             # daily 02:00 IST runs (APScheduler)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer

from atlas import config
from atlas.db.session import checkpoint, get_session, init_db, rebuild_fts, sync_sources

app = typer.Typer(add_completion=False, help="Scholarship Intelligence Crawler")
LOG_DIR = config.DATA_DIR / "logs"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("atlas")


def _scrapy(spider: str, run_id: int, sources: str | None = None, force: bool = False) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logf = LOG_DIR / f"run{run_id}_{spider}.log"
    cmd = [sys.executable, "-m", "scrapy", "crawl", spider, "-a", f"run_id={run_id}", "-a", f"force={int(force)}",
           "-s", f"LOG_FILE={logf}"]
    if sources:
        cmd += ["-a", f"sources={sources}"]
    env = dict(os.environ, PYTHONPATH=str(config.ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    log.info("scrapy %s (log: %s)", spider, logf)
    return subprocess.call(cmd, cwd=config.ROOT / "crawler", env=env)


def _new_run(mode: str, trigger: str) -> int:
    from atlas.db.models import CrawlRun
    with get_session() as s:
        r = CrawlRun(mode=mode, trigger=trigger, status="RUNNING")
        s.add(r)
        s.commit()
        return r.id


def _finish_run(run_id: int, stats: dict, status: str = "DONE") -> None:
    from sqlalchemy import func

    from atlas.db.models import CrawlRun, Page, RunEvent
    with get_session() as s:
        r = s.get(CrawlRun, run_id)
        r.finished_at = datetime.utcnow().replace(microsecond=0)
        r.status = status
        r.pages_fetched = s.query(func.count(Page.id)).filter(Page.last_run_id == run_id).scalar()
        r.pages_not_modified = s.query(func.count(Page.id)).filter(Page.last_run_id == run_id, Page.http_status == 304).scalar()
        r.errors = s.query(func.count(Page.id)).filter(Page.last_run_id == run_id, Page.consecutive_failures > 0).scalar()
        ev = dict(s.query(RunEvent.event, func.count(RunEvent.id)).filter(RunEvent.run_id == run_id).group_by(RunEvent.event).all())
        r.new_count, r.changed_count = ev.get("NEW", 0), ev.get("CHANGED", 0)
        r.unchanged_count, r.missing_count, r.expired_count = ev.get("UNCHANGED", 0), ev.get("MISSING", 0), ev.get("EXPIRED", 0)
        r.stats_json = json.dumps({**stats, "events": ev}, default=str)
        s.commit()


@app.command()
def init():
    """Create the database schema and load config/sources.yaml."""
    init_db()
    n = sync_sources()
    typer.echo(f"DB ready at {config.DB_URL}; {n} seed sources synced")


@app.command()
def crawl(spider: str = "official", run_id: int = typer.Option(None), sources: str = None, force: bool = False):
    """Run one spider (official | leads | recheck)."""
    rid = run_id or _new_run(spider.upper(), "MANUAL")
    _scrapy(spider, rid, sources, force)


@app.command()
def process(run_id: int = typer.Option(None), reprocess: bool = False):
    """Classify + extract new snapshots."""
    from atlas.process import process_pages
    typer.echo(json.dumps(process_pages(run_id, reprocess)))


@app.command()
def enrich(run_id: int = typer.Option(None)):
    """Fill card records from their (single-scheme) official guideline documents."""
    from atlas.process import enrich_card_records
    typer.echo(json.dumps(enrich_card_records(run_id)))


@app.command()
def verify(run_id: int = typer.Option(None)):
    from atlas.verify.confidence import verify_all
    typer.echo(json.dumps(verify_all(run_id)))


@app.command()
def lifecycle(run_id: int = typer.Option(None)):
    from atlas.lifecycle.status import update_lifecycle
    typer.echo(json.dumps(update_lifecycle(run_id)))


@app.command()
def resolve(run_id: int = typer.Option(None)):
    from atlas.discovery.resolver import resolve_leads
    typer.echo(json.dumps(resolve_leads(run_id)))


@app.command()
def run(force: bool = typer.Option(False, help="ignore recrawl schedule"), sources: str = None,
        skip_leads: bool = False, trigger: str = "MANUAL"):
    """Full pipeline: recheck -> leads -> resolve -> official crawl -> process -> verify -> lifecycle."""
    from atlas.discovery.resolver import resolve_leads
    from atlas.lifecycle.status import update_lifecycle
    from atlas.process import enrich_card_records, process_pages, touch_unchanged_scholarships
    from atlas.verify.confidence import verify_all

    init_db()
    sync_sources()
    rid = _new_run("FULL", trigger)
    t0 = datetime.utcnow()
    log.info("=== RUN %s started ===", rid)
    stats: dict = {}
    _scrapy("recheck", rid, None, force)
    if not skip_leads and not sources:
        _scrapy("leads", rid, None, force)
        stats["leads"] = resolve_leads(rid)
    _scrapy("official", rid, sources, force)
    _post(rid, stats, t0)


def run_health(rid: int) -> dict:
    """How many official scholarship pages re-checked in this run failed at the network level."""
    from atlas.db.models import Page, Scholarship
    with get_session() as s:
        ids = {pid for (pid,) in s.query(Scholarship.official_source_page_id)
               .filter(Scholarship.official_source_page_id.isnot(None))}
        pages = s.query(Page).filter(Page.id.in_(ids), Page.last_run_id == rid).all() if ids else []
        failed = [p for p in pages if p.consecutive_failures and (p.http_status or 0) in (0, 403, 429, 500, 502, 503, 504)]
        return {"checked": len(pages), "failed": len(failed),
                "failed_domains": sorted({(p.final_url or p.url).split("/")[2] for p in failed})[:20]}


DEGRADED_RATIO = 0.5


def _post(rid: int, stats: dict, t0: datetime) -> None:
    from atlas.lifecycle.status import update_lifecycle
    from atlas.process import enrich_card_records, process_pages, touch_unchanged_scholarships
    from atlas.verify.confidence import verify_all
    stats["process"] = process_pages(rid)
    stats["enrich"] = enrich_card_records(rid)
    stats["unchanged_touch"] = touch_unchanged_scholarships(rid)
    health = run_health(rid)
    stats["health"] = health
    degraded = health["checked"] >= 10 and health["failed"] / health["checked"] > DEGRADED_RATIO
    if degraded:
        # Most official pages were unreachable (outage, or the runner's IP is blocked/throttled). Do NOT
        # downgrade verification or count misses on that basis: keep the previous scores and statuses.
        log.warning("DEGRADED run %s: %d/%d official pages failed (%s) - verification/lifecycle not updated",
                    rid, health["failed"], health["checked"], ", ".join(health["failed_domains"][:8]))
        stats["degraded"] = True
    else:
        stats["verify"] = verify_all(rid)
        stats["lifecycle"] = update_lifecycle(rid)
    rebuild_fts()
    stats["duration_s"] = (datetime.utcnow() - t0).seconds
    _finish_run(rid, stats, status="DEGRADED" if degraded else "DONE")
    checkpoint()
    log.info("=== RUN %s finished: %s ===", rid, json.dumps(stats, default=str))
    report(rid)


@app.command()
def postprocess(run_id: int):
    """Resume the post-crawl stages (process -> enrich -> verify -> lifecycle) for a run."""
    from atlas.db.models import CrawlRun
    with get_session() as s:
        t0 = s.get(CrawlRun, run_id).started_at
    _post(run_id, {"resumed": True}, t0)


@app.command()
def rebuild(yes: bool = typer.Option(False, help="also discard recorded LIVE change history")):
    """Re-derive all records from stored snapshots (after improving extractors). Keeps pages/snapshots.
    Results are attributed to the latest crawl run."""
    from atlas.db.models import (CrawlRun, FieldChange, FieldEvidence, Page, RunEvent, Scholarship,
                                 ScholarshipSource, ScholarshipVersion, VerificationCheck)
    from atlas.lifecycle.status import update_lifecycle
    from atlas.process import process_pages
    from atlas.verify.confidence import verify_all
    with get_session() as s:
        live_changes = s.query(FieldChange).filter(FieldChange.origin == "LIVE", FieldChange.change_kind != "STATUS").count()
        if live_changes and not yes:
            typer.echo(f"refusing: {live_changes} recorded live field changes would be lost (use --yes)")
            raise typer.Exit(1)
        for m in (FieldChange, FieldEvidence, RunEvent, ScholarshipSource, ScholarshipVersion, VerificationCheck, Scholarship):
            s.query(m).delete()
        s.query(Page).update({"processed_snapshot_sha": None})
        s.commit()
        last = s.query(CrawlRun).order_by(CrawlRun.id.desc()).first()
        rid = last.id if last else None
    from atlas.process import enrich_card_records
    stats = {"rebuild": True, "process": process_pages(rid, reprocess=True), "enrich": enrich_card_records(rid),
             "verify": verify_all(rid), "lifecycle": update_lifecycle(rid)}
    rebuild_fts()
    if rid:
        _finish_run(rid, stats)
    typer.echo(json.dumps(stats))


@app.command()
def inspect(url_like: str, chars: int = 2500):
    """Debug: show text, sections, verdict and extracted fields for a stored page."""
    from atlas import snapshots
    from atlas.db.models import Page, Source
    from atlas.extract.record import build_record
    from atlas.process import analyse
    with get_session() as s:
        page = s.query(Page).filter(Page.url.like(f"%{url_like}%"), Page.latest_snapshot_sha.isnot(None)).first()
        if not page:
            typer.echo("not found")
            return
        src = s.get(Source, page.source_id)
        raw = snapshots.load_raw(page.latest_snapshot_sha)
        doc, sections, verdict = analyse(raw, page.final_url or page.url, page.content_type, page.url)
        typer.echo(f"URL {page.url}\nverdict {verdict}\nh1 {doc.h1[:3]}\ntitle {doc.title}\nsections "
                   f"{ {k: v.heading.text[:40] for k, v in sections.items()} }\n---\n{doc.text[:chars]}\n---")
        d = build_record(doc, sections, official_domain=src.registered_domain, registry_provider=src.provider_name,
                         source_name=src.name, domestic=True)
        typer.echo(f"rejected: {d.rejected_reason}")
        for k, f in d.fields.items():
            typer.echo(f"  {k:<22} = {str(f.value)[:100]!r:<60} | {f.pass_} | ev: {f.evidence[:120]!r}")


@app.command()
def report(run_id: int = typer.Argument(None)):
    """Human-readable run summary (Run N: A unchanged, B changed, ...)."""
    from atlas.db.models import CrawlRun, FieldChange, RunEvent, Scholarship
    with get_session() as s:
        r = s.get(CrawlRun, run_id) if run_id else s.query(CrawlRun).order_by(CrawlRun.id.desc()).first()
        if not r:
            typer.echo("no runs yet")
            return
        typer.echo(f"\nRun {r.id} ({r.mode}, {r.started_at} -> {r.finished_at}): pages fetched {r.pages_fetched}, "
                   f"304 {r.pages_not_modified}, errors {r.errors}")
        for ev in s.query(RunEvent).filter_by(run_id=r.id).order_by(RunEvent.event, RunEvent.id):
            sch = s.get(Scholarship, ev.scholarship_id) if ev.scholarship_id else None
            extra = ""
            if ev.event == "CHANGED":
                ch = s.query(FieldChange).filter_by(run_id=r.id, scholarship_id=ev.scholarship_id).all()
                extra = "; ".join(f"{c.field_name}: {c.old_value_json} -> {c.new_value_json}" for c in ch)[:300]
            elif ev.detail_json:
                extra = ev.detail_json[:160]
            typer.echo(f"  {ev.event:<15} {sch.name[:70] if sch else '-':<70} {extra}")


@app.command()
def stats():
    """Database summary (assignment minimum-output checks)."""
    from sqlalchemy import func

    from atlas.db.models import DiscoveryLead, FieldChange, Page, Scholarship, Source
    with get_session() as s:
        q = s.query(Scholarship).filter(Scholarship.is_active_record.is_(True))
        out = {
            "scholarships": q.count(),
            "official_source_records": q.join(Source, Source.id == Scholarship.source_id)
                                        .filter(Source.is_official.is_(True)).count(),
            "verified": q.filter(Scholarship.verification_status == "VERIFIED").count(),
            "confidence_ge_95": q.filter(Scholarship.confidence >= 95).count(),
            "by_status": dict(s.query(Scholarship.status, func.count()).group_by(Scholarship.status).all()),
            "by_source_type": dict(s.query(Scholarship.source_type, func.count()).group_by(Scholarship.source_type).all()),
            "discovered_beyond_seeds": q.filter(Scholarship.discovered_via != "SEED").count(),
            "field_changes": dict(s.query(FieldChange.origin, func.count()).group_by(FieldChange.origin).all()),
            "pages": s.query(func.count(Page.id)).scalar(),
            "pages_by_kind": dict(s.query(Page.page_kind, func.count()).group_by(Page.page_kind).all()),
            "sources": dict(s.query(Source.origin, func.count()).group_by(Source.origin).all()),
            "leads": dict(s.query(DiscoveryLead.status, func.count()).group_by(DiscoveryLead.status).all()),
            "avg_confidence": round(q.with_entities(func.avg(Scholarship.confidence)).scalar() or 0, 1),
        }
        typer.echo(json.dumps(out, indent=2, default=str))


@app.command()
def wayback(limit: int = 25, min_age_days: int = 120):
    """Compare current records with archived official pages (labelled historical baselines)."""
    from atlas.discovery.wayback import wayback_baseline
    typer.echo(json.dumps(wayback_baseline(limit=limit, min_age_days=min_age_days)))


@app.command()
def export(out_dir: Path = config.DATA_DIR / "sample"):
    """Export scholarships (+ evidence) to JSON and CSV."""
    import csv

    from atlas.db.models import FieldEvidence, Scholarship
    out_dir.mkdir(parents=True, exist_ok=True)
    with get_session() as s:
        rows = []
        for sch in s.query(Scholarship).order_by(Scholarship.confidence.desc()):
            rec = {c.name: getattr(sch, c.name) for c in Scholarship.__table__.columns
                   if c.name not in ("field_values_json", "confidence_breakdown")}
            rec["evidence"] = [{"field": e.field_name, "value": json.loads(e.value_json), "evidence": e.evidence_text,
                                "source_url": e.source_url, "snapshot_sha256": e.snapshot_sha256, "locator": e.locator}
                               for e in s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True)]
            rec["confidence_breakdown"] = json.loads(sch.confidence_breakdown or "{}")
            rows.append(rec)
    (out_dir / "scholarships.json").write_text(json.dumps(rows, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    flat_cols = [k for k in rows[0] if k not in ("evidence", "confidence_breakdown")] if rows else []
    with open(out_dir / "scholarships.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=flat_cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    typer.echo(f"exported {len(rows)} records to {out_dir}")


@app.command()
def audit(n: int = 20, out: Path = config.DATA_DIR / "audit_sample.csv", seed: int = 7):
    """Random sample of records + every evidence snippet, for manual field-by-field checking."""
    import csv
    import random

    from atlas.db.models import FieldEvidence, Scholarship
    with get_session() as s:
        recs = s.query(Scholarship).all()
        random.Random(seed).shuffle(recs)
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["scholarship_id", "name", "confidence", "status", "field", "value", "evidence", "source_url",
                        "correct? (Y/N)"])
            for sch in recs[:n]:
                for e in s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True):
                    w.writerow([sch.id, sch.name, sch.confidence, sch.status, e.field_name, e.value_json,
                                e.evidence_text[:500], e.source_url, ""])
    typer.echo(f"audit sheet written to {out}")


@app.command("checkpoint")
def checkpoint_cmd():
    """Write the WAL into data/atlas.db (run before committing the DB)."""
    checkpoint()
    typer.echo("database checkpointed (single-file journal)")


@app.command("evidence-files")
def evidence_files():
    """Print snapshot files referenced by evidence (so CI can commit raw copies of evidence pages only)."""
    from atlas import snapshots
    from atlas.db.models import FieldEvidence
    with get_session() as s:
        shas = {sha for (sha,) in s.query(FieldEvidence.snapshot_sha256).filter(FieldEvidence.snapshot_sha256.isnot(None))}
    for sha in sorted(shas):
        for p in (snapshots.raw_path(sha), snapshots.text_path(sha)):
            if p.exists():
                typer.echo(p.relative_to(config.ROOT).as_posix())


@app.command()
def schedule(hour_ist: int = 2, minute: int = 0):
    """Run the full pipeline daily at the given IST time (off-peak)."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    sched = BlockingScheduler(timezone="Asia/Kolkata")
    sched.add_job(lambda: run(force=False, sources=None, skip_leads=False, trigger="SCHEDULED"),
                  "cron", hour=hour_ist, minute=minute)
    typer.echo(f"scheduler started: daily at {hour_ist:02d}:{minute:02d} IST")
    sched.start()


if __name__ == "__main__":
    app()
