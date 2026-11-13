"""Historical baseline via the Internet Archive Wayback Machine (free, no key).

For an official page we fetch an archived copy from >= `min_age_days` ago, extract it with the same
deterministic pipeline (single-scheme pages AND card listings) and compare it with the current record.
Differences are REAL changes on the official source over time; they are stored as
FieldChange(origin='WAYBACK_BASELINE') with the archive URL as the old source, and are labelled as
historical in the UI. The current record itself is never modified by this step.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import requests

from atlas import config, snapshots
from atlas.db.models import FieldChange, FieldEvidence, Page, Scholarship, Snapshot, Source
from atlas.db.session import get_session
from atlas.extract.cards import extract_cards
from atlas.extract.record import build_record
from atlas.process import analyse, norm_name, norm_value, prune_untraceable, same_scheme

log = logging.getLogger("atlas.wayback")
CDX = "https://web.archive.org/cdx/search/cdx"
COMPARE = ("closing_date", "opening_date", "amount", "academic_year", "income_limit", "age", "education_levels",
           "gender", "categories", "application_url", "page_status")


def find_snapshot(url: str, min_age_days: int) -> tuple[str, str] | None:
    to = (datetime.utcnow() - timedelta(days=min_age_days)).strftime("%Y%m%d")
    r = requests.get(CDX, params={"url": url, "output": "json", "filter": "statuscode:200", "to": to,
                                  "limit": "-1", "fl": "timestamp,original"},
                     headers={"User-Agent": config.USER_AGENT}, timeout=60)
    if r.status_code != 200:
        return None
    rows = r.json()
    if len(rows) < 2:
        return None
    return rows[-1][0], rows[-1][1]


def _fetch_archive(page: Page, min_age_days: int, delay: float):
    snap = find_snapshot(page.url, min_age_days)
    time.sleep(delay)
    if not snap:
        return None
    ts, orig = snap
    archive_url = f"https://web.archive.org/web/{ts}id_/{orig}"
    resp = requests.get(archive_url, headers={"User-Agent": config.USER_AGENT}, timeout=90)
    time.sleep(delay)
    if resp.status_code != 200 or not resp.content:
        return None
    return ts, archive_url, resp


def wayback_baseline(limit: int = 25, min_age_days: int = 120, delay: float = 3.0, run_id=None) -> dict:
    stats = {"pages_checked": 0, "archived": 0, "records_compared": 0, "changes": 0}
    with get_session() as s:
        done = {sid for (sid,) in s.query(FieldChange.scholarship_id).filter(FieldChange.origin == "WAYBACK_BASELINE")}
        rows = (s.query(Scholarship, Page, Source).join(Page, Scholarship.official_source_page_id == Page.id)
                .join(Source, Scholarship.source_id == Source.id)
                .filter(Scholarship.is_active_record.is_(True)).all())
        by_page: dict[int, list] = {}
        for sch, page, src in rows:
            if sch.id not in done:
                by_page.setdefault(page.id, []).append((sch, page, src))
        for pid, group in list(by_page.items())[:limit]:
            _, page, src = group[0]
            stats["pages_checked"] += 1
            try:
                got = _fetch_archive(page, min_age_days, delay)
            except requests.RequestException as e:
                log.warning("wayback error %s: %s", page.url, e)
                continue
            if not got:
                continue
            ts, archive_url, resp = got
            stats["archived"] += 1
            raw = resp.content
            sha = snapshots.store_raw(raw)
            doc, sections, _ = analyse(raw, page.final_url or page.url, resp.headers.get("Content-Type"), page.url)
            snapshots.store_text(sha, doc.text)
            if s.get(Snapshot, sha) is None:
                s.add(Snapshot(sha256=sha, page_id=page.id, url=page.url, http_status=200,
                               content_type=resp.headers.get("Content-Type"), raw_path=str(snapshots.raw_path(sha)),
                               text_path=str(snapshots.text_path(sha)), origin="WAYBACK", archive_url=archive_url,
                               fetched_at=datetime.strptime(ts[:14], "%Y%m%d%H%M%S"), run_id=run_id))
            # archived drafts: card listing -> one draft per scheme; otherwise the single-scheme page
            drafts = []
            if any(sch.record_kind == "CARD" for sch, _, _ in group):
                drafts = [d for d, _ in extract_cards(raw, page.final_url or page.url, doc)]
            else:
                d = build_record(doc, sections, official_domain=src.registered_domain,
                                 registry_provider=src.provider_name, source_name=src.name, domestic=True)
                if not d.rejected_reason and "name" in d.fields:
                    drafts = [d]
            for d in drafts:
                prune_untraceable(d, doc, raw)
            for sch, _, _ in group:
                match = next((d for d in drafts if "name" in d.fields and same_scheme(str(d.value("name")), sch.name)), None)
                if match is None:
                    continue
                stats["records_compared"] += 1
                old_vals = {k: norm_value(k, f.value) for k, f in match.fields.items()}
                cur = json.loads(sch.field_values_json or "{}")
                cur_ev = {e.field_name: e for e in s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True)}
                for k in COMPARE:
                    o, n = old_vals.get(k), cur.get(k)
                    if o is None or n is None:
                        continue           # only compare values evidenced in BOTH versions
                    if json.dumps(o, sort_keys=True, default=str) == json.dumps(n, sort_keys=True, default=str):
                        continue
                    s.add(FieldChange(scholarship_id=sch.id, field_name=k, old_value_json=json.dumps(o, default=str),
                                      new_value_json=json.dumps(n, default=str), change_kind="UPDATED",
                                      detected_at=datetime.utcnow(), run_id=run_id, source_url=sch.official_source_url,
                                      old_source_url=archive_url, old_evidence_text=match.fields[k].evidence,
                                      new_evidence_text=cur_ev[k].evidence_text if k in cur_ev else None,
                                      new_evidence_id=cur_ev[k].id if k in cur_ev else None,
                                      origin="WAYBACK_BASELINE"))
                    stats["changes"] += 1
            s.commit()
    return stats
