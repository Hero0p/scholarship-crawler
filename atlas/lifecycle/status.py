"""Lifecycle statuses (PROJECT_PLAN.md §12) and stale/missing detection."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from atlas import snapshots
from atlas.db.models import FieldChange, Page, RunEvent, Scholarship, Source
from atlas.db.session import get_session
from atlas.verify.confidence import name_match

EXPIRING_DAYS = 15
MISSES_FOR_GONE = 2


def live_status(sch: Scholarship, page: Page | None, src: Source | None) -> tuple[str, str]:
    if page is None:
        return "UNKNOWN", "no official page"
    if src and src.approval_status == "BROKEN":
        return "DRIFTED", src.approval_reason or "source domain drifted"
    if page.page_kind == "DRIFTED":
        return "DRIFTED", f"page now redirects to {page.final_url}"
    if (page.http_status or 0) in (404, 410) or page.page_kind in ("GONE", "SOFT_404"):
        return "GONE", f"official page returns HTTP {page.http_status} / {page.page_kind}"
    if page.consecutive_failures:
        stale = page.last_ok_at is None or datetime.utcnow() - page.last_ok_at > timedelta(hours=72)
        if page.consecutive_failures >= 3 and stale:
            return "UNREACHABLE", f"{page.consecutive_failures} consecutive failed fetches over >72h"
        return "TEMP_FAILURE", f"{page.consecutive_failures} consecutive failed fetch(es)"
    txt = snapshots.load_text(page.latest_snapshot_sha) if page.latest_snapshot_sha else None
    if name_match(sch.name, txt) < 75:
        return "MISSING", "scholarship name no longer found on the official page"
    return "LIVE", "live"


def compute_status(sch: Scholarship, live: str, today: date) -> tuple[str, str]:
    if live == "DRIFTED":
        return "NO_LONGER_VERIFIABLE", "official source domain no longer belongs to the provider"
    if live in ("GONE", "MISSING", "UNREACHABLE") and sch.consecutive_misses >= MISSES_FOR_GONE:
        return "NO_LONGER_VERIFIABLE", f"{live.lower()} on {sch.consecutive_misses} consecutive checks"
    if sch.page_status_text == "DISCONTINUED":
        return "REVIEW_REQUIRED", "official page indicates the scheme is discontinued/withdrawn"
    if sch.closing_date and sch.closing_date < today:
        return "EXPIRED", f"closing date {sch.closing_date} has passed (re-checked after the deadline, no extension found)"
    if sch.page_status_text == "CLOSED" and not (sch.closing_date and sch.closing_date >= today):
        return "EXPIRED", "official page states applications are closed"
    if sch.verification_status != "VERIFIED":
        return "REVIEW_REQUIRED", f"confidence {sch.confidence} below 95 or a gate failed"
    if sch.closing_date and sch.closing_date <= today + timedelta(days=EXPIRING_DAYS):
        return "EXPIRING_SOON", f"closes on {sch.closing_date}"
    if (sch.closing_date and sch.closing_date > today) or sch.is_rolling or sch.page_status_text == "OPEN":
        return "ACTIVE", "verified and open"
    return "REVIEW_REQUIRED", "verified but no evidence that applications are currently open"


def update_lifecycle(run_id=None) -> dict:
    today = date.today()
    counts: dict[str, int] = {}
    with get_session() as s:
        new_ids = {sid for (sid,) in s.query(RunEvent.scholarship_id).filter(
            RunEvent.run_id == run_id, RunEvent.event == "NEW")} if run_id is not None else set()
        for sch in s.query(Scholarship).filter(Scholarship.is_active_record.is_(True)):
            page = s.get(Page, sch.official_source_page_id) if sch.official_source_page_id else None
            src = s.get(Source, sch.source_id) if sch.source_id else None
            live, why = live_status(sch, page, src)
            checked_now = page is not None and run_id is not None and page.last_run_id == run_id
            if checked_now:
                if live in ("GONE", "MISSING", "UNREACHABLE", "DRIFTED"):
                    sch.consecutive_misses = (sch.consecutive_misses or 0) + 1
                    s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="MISSING",
                                   detail_json=json.dumps({"live": live, "why": why})))
                elif live == "LIVE":
                    if sch.consecutive_misses:
                        s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="REAPPEARED",
                                       detail_json=json.dumps({"after_misses": sch.consecutive_misses})))
                    sch.consecutive_misses = 0
            sch.live_status = live
            status, reason = compute_status(sch, live, today)
            if status != sch.status:
                if sch.id not in new_ids and run_id is not None:
                    s.add(FieldChange(scholarship_id=sch.id, field_name="status",
                                      old_value_json=json.dumps(sch.status), new_value_json=json.dumps(status),
                                      change_kind="STATUS", run_id=run_id, source_url=sch.official_source_url,
                                      new_evidence_text=reason, origin="LIVE"))
                    s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="STATUS_CHANGED",
                                   detail_json=json.dumps({"from": sch.status, "to": status, "why": reason})))
                    if status == "EXPIRED":
                        s.add(RunEvent(run_id=run_id, scholarship_id=sch.id, event="EXPIRED",
                                       detail_json=json.dumps({"why": reason})))
                sch.status = status
            counts[status] = counts.get(status, 0) + 1
        s.commit()
    return counts
