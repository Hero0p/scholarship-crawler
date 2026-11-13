"""Polite-crawling middlewares: conditional GET, per-domain circuit breaker, Retry-After."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from scrapy import signals
from scrapy.exceptions import IgnoreRequest

from atlas.urls import registered_domain


def abort_heavy_resources(request) -> bool:
    """Playwright: only load documents, scripts and XHR."""
    return request.resource_type in ("image", "media", "font", "stylesheet")


class ConditionalGetMiddleware:
    """Adds If-None-Match / If-Modified-Since for re-checks (304 => unchanged)."""

    def process_request(self, request, spider=None):
        if request.meta.get("conditional"):
            if request.meta.get("etag"):
                request.headers.setdefault("If-None-Match", request.meta["etag"])
            if request.meta.get("last_modified"):
                request.headers.setdefault("If-Modified-Since", request.meta["last_modified"])
        return None


class DomainCircuitBreakerMiddleware:
    """>= THRESHOLD consecutive 403/429/5xx/timeouts on a domain => skip it for the rest of the
    run and pause it for 24 h (recorded in sources.paused_until)."""

    THRESHOLD = 5
    BAD = {403, 429, 500, 502, 503, 504, 520, 521, 522, 524}

    def __init__(self, crawler=None):
        self.crawler = crawler
        self.fails: dict[str, int] = defaultdict(int)
        self.tripped: set[str] = set()

    @classmethod
    def from_crawler(cls, crawler):
        mw = cls(crawler)
        crawler.signals.connect(mw.spider_closed, signal=signals.spider_closed)
        return mw

    def process_request(self, request, spider=None):
        dom = registered_domain(request.url)
        if dom in self.tripped and not request.meta.get("bypass_breaker"):
            raise IgnoreRequest(f"circuit open for {dom}")
        return None

    def process_response(self, request, response, spider=None):
        dom = registered_domain(request.url)
        if response.status in self.BAD:
            self._fail(dom)
        else:
            self.fails[dom] = 0
        return response

    def process_exception(self, request, exception, spider=None):
        self._fail(registered_domain(request.url))
        return None

    def _fail(self, dom: str):
        self.fails[dom] += 1
        if self.fails[dom] >= self.THRESHOLD and dom not in self.tripped:
            self.tripped.add(dom)
            if self.crawler and self.crawler.stats:
                self.crawler.stats.inc_value("atlas/circuit_breaker_tripped")

    def spider_closed(self, spider):
        if not self.tripped:
            return
        from atlas.db.models import Source
        from atlas.db.session import get_session
        with get_session() as s:
            until = datetime.utcnow() + timedelta(hours=24)
            for src in s.query(Source).filter(Source.registered_domain.in_(self.tripped)):
                src.paused_until = until
                src.consecutive_failures = (src.consecutive_failures or 0) + 1
            s.commit()
        spider.logger.warning("Circuit breaker paused domains for 24h: %s", sorted(self.tripped))


class RetryAfterMiddleware:
    """Honour Retry-After on 429/503 without blocking the reactor: raise the download-slot delay
    for that host so every later request to it waits at least Retry-After seconds."""

    MAX_WAIT = 120

    def __init__(self, crawler=None):
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_response(self, request, response, spider=None):
        if response.status in (429, 503):
            ra = response.headers.get(b"Retry-After")
            try:
                wait = min(int(ra.decode().strip()), self.MAX_WAIT) if ra else 10
            except ValueError:
                wait = 10
            try:
                slot_key = request.meta.get("download_slot") or registered_domain(request.url)
                slots = self.crawler.engine.downloader.slots
                for key, slot in slots.items():
                    if key == slot_key or key.endswith(slot_key):
                        slot.delay = max(slot.delay, float(wait))
            except Exception:  # noqa: BLE001 - best effort only
                pass
        return response
