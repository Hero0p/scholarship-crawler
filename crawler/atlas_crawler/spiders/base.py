"""Shared helpers for Atlas spiders."""
from __future__ import annotations

import json
from datetime import datetime

import scrapy

from atlas.db.models import Page, Source
from atlas.db.session import get_session
from atlas.urls import canonicalize


def page_item(response, meta: dict, run_id) -> dict:
    ct = response.headers.get(b"Content-Type", b"").decode("latin-1")
    return {
        "type": "page",
        "url": meta.get("orig_url") or response.request.meta.get("redirect_urls", [response.url])[0],
        "final_url": response.url,
        "status": response.status,
        "content_type": ct,
        "etag": (response.headers.get(b"ETag") or b"").decode("latin-1") or None,
        "last_modified": (response.headers.get(b"Last-Modified") or b"").decode("latin-1") or None,
        "body": response.body if response.status == 200 else None,
        "source_id": meta.get("source_id"),
        "depth": meta.get("atlas_depth", 0),
        "from_url": meta.get("from_url"),
        "run_id": run_id,
    }


def error_item(failure, run_id) -> dict:
    req = failure.request
    status = getattr(getattr(failure.value, "response", None), "status", 0) or 0
    return {"type": "page", "url": req.meta.get("orig_url") or req.url, "final_url": req.url,
            "status": status, "body": None, "source_id": req.meta.get("source_id"),
            "depth": req.meta.get("atlas_depth", 0), "from_url": req.meta.get("from_url"), "run_id": run_id}


def load_sources(keys: list[str] | None = None, roles=("CRAWL",), include_discovered=True) -> list[Source]:
    now = datetime.utcnow()
    with get_session() as s:
        q = s.query(Source).filter(Source.approval_status == "APPROVED", Source.role.in_(roles))
        if not include_discovered:
            q = q.filter(Source.origin == "SEED")
        out = []
        for src in q.all():
            if keys and src.registry_key not in keys:
                continue
            if src.paused_until and src.paused_until > now:
                continue
            out.append(src)
        return out


def fetched_this_run(run_id) -> set[str]:
    if run_id is None:
        return set()
    with get_session() as s:
        return {c for (c,) in s.query(Page.canonical_url).filter(Page.last_run_id == int(run_id))}


class AtlasSpider(scrapy.Spider):
    custom_settings: dict = {}

    def __init__(self, run_id=None, sources=None, force="0", *a, **kw):
        super().__init__(*a, **kw)
        self.run_id = int(run_id) if run_id not in (None, "", "None") else None
        self.source_keys = [k for k in (sources or "").split(",") if k]
        self.force = str(force) in ("1", "true", "True")
        self.seen: set[str] = set()

    async def start(self):
        """Scrapy >= 2.13 entry point; delegates to the classic start_requests() generator."""
        for req in self.start_requests():
            yield req

    def mark_seen(self, url: str) -> bool:
        c = canonicalize(url)
        if c in self.seen:
            return False
        self.seen.add(c)
        return True

    def on_error(self, failure):
        from scrapy.exceptions import IgnoreRequest
        if failure.check(IgnoreRequest):   # robots.txt disallow / circuit breaker: not a page failure
            self.crawler.stats.inc_value("atlas/ignored_requests")
            return
        yield error_item(failure, self.run_id)

    @staticmethod
    def json_list(v) -> list:
        try:
            return json.loads(v or "[]")
        except ValueError:
            return []
