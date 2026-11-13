"""Seeded, bounded, focused crawl over approved official sources (PROJECT_PLAN.md §7).

- starts from each source's seed URLs (hubs)
- follows in-domain links best-first by link score, within max_depth and page_budget
- follows relevant PDFs
- off-domain links to official TLDs (gov.in / nic.in / ac.in / res.in / edu ...) with a
  scholarship-like anchor are registered as DISCOVERED sources and crawled with a small budget
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from urllib.parse import urljoin

import scrapy
from scrapy.http import TextResponse

from atlas.classify.page_classifier import link_score, url_denied
from atlas.classify.source_classifier import (classify_by_rules, is_aggregator, is_ignored_host,
                                              tld_class)
from atlas.db.models import Page, Source
from atlas.db.session import get_session
from atlas.urls import canonicalize, registered_domain

from .base import AtlasSpider, fetched_this_run, load_sources, page_item

DISCOVERED_BUDGET = 15
DISCOVERED_DEPTH = 1
MAX_NEW_DOMAINS = 40


class OfficialSpider(AtlasSpider):
    name = "official"

    def start_requests(self):
        self.budget = defaultdict(int)
        self.src_by_id: dict[int, dict] = {}
        self.new_domains: set[str] = set()
        self.seen |= fetched_this_run(self.run_id)   # pages already fetched by the recheck spider
        now = datetime.utcnow()
        with get_session() as s:
            not_due = {c for (c,) in s.query(Page.canonical_url).filter(
                Page.next_crawl_at.isnot(None), Page.next_crawl_at > now)}
        self.not_due = set() if self.force else not_due
        for src in load_sources(self.source_keys or None):
            info = {"id": src.id, "key": src.registry_key, "domain": src.registered_domain,
                    "aliases": {registered_domain("http://" + a) for a in self.json_list(src.domain_aliases)},
                    "max_depth": src.max_depth, "budget": src.page_budget, "render_js": src.render_js}
            self.src_by_id[src.id] = info
            for seed in self.json_list(src.seeds):
                yield from self._request(seed, info, depth=0, from_url=None, priority=1000, seed=True)

    # ------------------------------------------------------------------ helpers
    def _request(self, url, info, depth, from_url, priority, seed=False):
        if not self.mark_seen(url):
            return
        if not seed and canonicalize(url) in self.not_due:
            return
        if self.budget[info["id"]] >= info["budget"]:
            return
        self.budget[info["id"]] += 1
        meta = {"source_id": info["id"], "atlas_depth": depth, "from_url": from_url, "orig_url": url,
                "handle_httpstatus_list": [404, 410]}
        if info.get("render_js") and self.settings.getbool("PLAYWRIGHT_AVAILABLE") and not url.lower().endswith(".pdf"):
            meta["playwright"] = True
        yield scrapy.Request(url, callback=self.parse, errback=self.on_error, meta=meta, priority=priority,
                             dont_filter=True)

    def _in_scope(self, url, info) -> bool:
        reg = registered_domain(url)
        return reg == info["domain"] or reg in info["aliases"]

    # ------------------------------------------------------------------ parse
    def parse(self, response):
        meta = response.meta
        yield page_item(response, meta, self.run_id)
        if response.status != 200:
            return
        if not isinstance(response, TextResponse):      # PDFs etc.: stored, not link-followed
            return
        info = self.src_by_id.get(meta["source_id"])
        if info is None:
            return
        depth = meta.get("atlas_depth", 0)
        if depth >= info["max_depth"]:
            return
        for a in response.css("a[href]"):
            href = (a.attrib.get("href") or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(response.url, href).split("#")[0]
            if not url.startswith("http") or url_denied(url):
                continue
            anchor = " ".join(a.css("::text").getall()).strip() or a.attrib.get("title", "")
            score = link_score(anchor, url)
            if self._in_scope(url, info):
                if score >= 2 or (depth == 0 and score >= 1):
                    yield from self._request(url, info, depth + 1, response.url, priority=10 * score - depth)
            elif score >= 2:
                yield from self._discover_domain(url, anchor, response.url)

    def _discover_domain(self, url, anchor, from_url):
        """Outlink expansion: official-TLD domains become DISCOVERED sources (auto-approved by rule)."""
        if is_ignored_host(url) or is_aggregator(url) or tld_class(url) is None:
            return
        dom = registered_domain(url)
        known = {i["domain"] for i in self.src_by_id.values()}
        if dom in known:
            return
        if dom not in self.new_domains and len(self.new_domains) >= MAX_NEW_DOMAINS:
            return
        info = self._ensure_discovered_source(dom, url, anchor, from_url)
        if info:
            self.new_domains.add(dom)
            yield from self._request(url, info, 0, from_url, priority=500)

    def _ensure_discovered_source(self, dom, url, anchor, from_url):
        cls = classify_by_rules(url)
        with get_session() as s:
            src = s.query(Source).filter_by(registered_domain=dom).first()
            if src is None:
                src = Source(registry_key=f"disc:{dom}", name=dom, provider_name=None, registered_domain=dom,
                             domain_aliases="[]", seeds=json.dumps([url]), sitemaps="[]",
                             source_type=cls.source_type, trust_tier=cls.trust_tier, is_official=cls.is_official,
                             role="CRAWL", origin="DISCOVERED_OUTLINK",
                             approval_status="APPROVED" if cls.is_official else "PENDING",
                             approval_reason=f"auto: {cls.reason}; found via link '{anchor[:80]}'",
                             max_depth=DISCOVERED_DEPTH, page_budget=DISCOVERED_BUDGET,
                             base_frequency_hours=168, discovered_from=from_url)
                s.add(src)
                s.commit()
            if src.approval_status != "APPROVED" or src.role != "CRAWL":
                return None
            info = {"id": src.id, "key": src.registry_key, "domain": dom, "aliases": set(),
                    "max_depth": src.max_depth, "budget": src.page_budget, "render_js": False}
        self.src_by_id[info["id"]] = info
        return info
