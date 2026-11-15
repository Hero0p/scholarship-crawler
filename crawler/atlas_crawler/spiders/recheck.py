"""Re-verification crawl: revisits every page that is the official source of a scholarship (plus
other pages that are due), using conditional GETs. Powers change / stale detection."""
from __future__ import annotations

from datetime import datetime

import scrapy

from atlas.db.models import Page, Scholarship, ScholarshipSource
from atlas.db.session import get_session

from atlas.trace import short, trace

from .base import AtlasSpider, page_item


class RecheckSpider(AtlasSpider):
    name = "recheck"

    def start_requests(self):
        now = datetime.utcnow()
        with get_session() as s:
            ids = {pid for (pid,) in s.query(ScholarshipSource.page_id)}
            ids |= {pid for (pid,) in s.query(Scholarship.official_source_page_id)
                    .filter(Scholarship.official_source_page_id.isnot(None))}
            pages = s.query(Page).filter(Page.id.in_(ids)).all() if ids else []
            due = [p for p in pages if self.force or p.next_crawl_at is None or p.next_crawl_at <= now]
            self.logger.info("recheck: %d scholarship pages, %d due", len(pages), len(due))
            trace("STEP", f"re-verify known scholarship pages: {len(pages)} known, {len(due)} due now "
                          f"(conditional GET: 304 = unchanged)")
            for p in due:
                meta = {"source_id": p.source_id, "atlas_depth": p.depth, "orig_url": p.url,
                        "conditional": True, "etag": p.etag, "last_modified": p.last_modified,
                        "handle_httpstatus_list": [304, 404, 410], "bypass_breaker": True}
                if self.mark_seen(p.url):
                    yield scrapy.Request(p.url, callback=self.parse, errback=self.on_error, meta=meta,
                                         dont_filter=True, priority=100)

    def parse(self, response):
        verdict = {200: "fetched", 304: "not modified", 404: "GONE", 410: "GONE"}.get(response.status, "")
        trace("RECHECK", f"{response.status} {verdict:<12} {short(response.url)}")
        yield page_item(response, response.meta, self.run_id)
