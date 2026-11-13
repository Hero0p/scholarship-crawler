"""Persist fetched pages (+ immutable snapshots) and discovery leads into the Atlas DB."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from atlas import snapshots
from atlas.db.models import DiscoveryLead, Page, Snapshot
from atlas.db.session import get_session
from atlas.urls import canonicalize, registered_domain


class PersistPipeline:
    def open_spider(self, spider=None):
        self.session = get_session()
        self.n = 0

    def close_spider(self, spider=None):
        self.session.commit()
        self.session.close()

    def process_item(self, item, spider=None):
        kind = item.get("type")
        if kind == "page":
            self._page(item)
        elif kind == "lead":
            self._lead(item)
        self.n += 1
        self.session.commit()      # short write transactions: SQLite allows one writer at a time
        return item

    def _page(self, it: dict):
        s = self.session
        now = datetime.utcnow().replace(microsecond=0)
        canon = canonicalize(it["url"])
        page = s.query(Page).filter_by(canonical_url=canon).one_or_none()
        if page is None:
            page = Page(canonical_url=canon, url=it["url"], source_id=it.get("source_id"),
                        depth=it.get("depth", 0), discovered_from_url=it.get("from_url"), first_seen_at=now)
            s.add(page)
            s.flush()
        elif it.get("source_id") and not page.source_id:
            page.source_id = it["source_id"]
        page.last_fetched_at = now
        page.last_run_id = it.get("run_id")
        page.http_status = it.get("status")
        page.final_url = it.get("final_url") or it["url"]
        status = it.get("status") or 0
        if status == 304:
            page.consecutive_failures = 0
            page.last_ok_at = now
            page.unchanged_streak = (page.unchanged_streak or 0) + 1
            return
        if status == 0 or status >= 400 or it.get("body") is None:
            page.consecutive_failures = (page.consecutive_failures or 0) + 1
            if status in (404, 410):
                page.page_kind = "GONE"
            return
        page.consecutive_failures = 0
        page.last_ok_at = now
        page.etag = it.get("etag")
        page.last_modified = it.get("last_modified")
        page.content_type = it.get("content_type")
        body: bytes = it["body"]
        sha = snapshots.store_raw(body)
        if sha != page.latest_snapshot_sha:
            if s.get(Snapshot, sha) is None:
                s.add(Snapshot(sha256=sha, page_id=page.id, url=it["url"], http_status=status,
                               content_type=it.get("content_type"),
                               raw_path=str(snapshots.raw_path(sha)), origin="LIVE", run_id=it.get("run_id")))
            page.latest_snapshot_sha = sha
            page.last_changed_at = now
            page.unchanged_streak = 0
        else:
            page.unchanged_streak = (page.unchanged_streak or 0) + 1
        s.flush()

    def _lead(self, it: dict):
        s = self.session
        exists = s.query(DiscoveryLead).filter_by(found_on_url=it["found_on_url"],
                                                  outbound_url=it.get("outbound_url")).one_or_none()
        if exists:
            exists.run_id = it.get("run_id")
            return
        s.add(DiscoveryLead(lead_name=it.get("lead_name"), found_on_url=it["found_on_url"],
                            aggregator_domain=registered_domain(it["found_on_url"]),
                            outbound_url=it.get("outbound_url"),
                            outbound_domain=registered_domain(it["outbound_url"]) if it.get("outbound_url") else None,
                            status="NEW", run_id=it.get("run_id")))
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
