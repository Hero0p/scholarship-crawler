"""Aggregator lead mining — DISCOVERY ONLY (PROJECT_PLAN.md §7.2).

Aggregator pages are never used as a value source. From each aggregator scholarship page we keep
only (a) the scholarship name and (b) outbound links to non-aggregator sites. The resolver
(atlas.discovery.resolver) then decides whether an outbound domain is an official provider.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from urllib.parse import urljoin

import scrapy
from scrapy.http import TextResponse

from atlas.classify.page_classifier import link_score, url_denied
from atlas.classify.source_classifier import is_aggregator, is_ignored_host
from atlas.urls import registered_domain

from .base import AtlasSpider, load_sources

YEAR = date.today().year
FRESH_SLUG = re.compile(rf"({YEAR}|{YEAR + 1}|{YEAR - 1}-{str(YEAR)[2:]}|{YEAR}-{str(YEAR + 1)[2:]})")
OLD_SLUG = re.compile(r"20(1\d|2[0-" + str((YEAR - 2) % 10) + r"])(?!\d)")


class LeadsSpider(AtlasSpider):
    name = "leads"

    def start_requests(self):
        self.budget = defaultdict(int)
        self.info = {}
        for src in load_sources(self.source_keys or None, roles=("DISCOVERY_ONLY",)):
            info = {"id": src.id, "domain": src.registered_domain, "budget": src.page_budget}
            self.info[src.id] = info
            for sm in self.json_list(src.sitemaps):
                yield scrapy.Request(sm, callback=self.parse_sitemap, errback=self.on_error,
                                     meta={"source_id": src.id}, dont_filter=True)
            for seed in self.json_list(src.seeds):
                yield from self._req(seed, info, 0, priority=100)

    def _req(self, url, info, depth, priority=0):
        if not self.mark_seen(url) or self.budget[info["id"]] >= info["budget"]:
            return
        self.budget[info["id"]] += 1
        yield scrapy.Request(url, callback=self.parse, errback=self.on_error, priority=priority,
                             meta={"source_id": info["id"], "atlas_depth": depth}, dont_filter=True)

    def parse_sitemap(self, response):
        info = self.info[response.meta["source_id"]]
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", response.text)
        fresh = [u for u in locs if "/scholarship/" in u and FRESH_SLUG.search(u)]
        undated = [u for u in locs if "/scholarship/" in u and not FRESH_SLUG.search(u) and not OLD_SLUG.search(u)]
        self.logger.info("sitemap %s: %d urls, %d fresh, %d undated", response.url, len(locs), len(fresh), len(undated))
        for u in fresh + undated:
            yield from self._req(u, info, 1, priority=50 if u in fresh else 10)

    def parse(self, response):
        if response.status != 200 or not isinstance(response, TextResponse):
            return
        info = self.info[response.meta["source_id"]]
        depth = response.meta.get("atlas_depth", 0)
        h1 = " ".join(response.css("h1 ::text").getall()).strip()
        if h1 and re.search(r"scholarship|fellowship|award|grant|programme|scheme", h1, re.I):
            outs = set()
            for a in response.css("a[href]"):
                url = urljoin(response.url, a.attrib.get("href", "")).split("#")[0]
                if not url.startswith("http") or url_denied(url):
                    continue
                if is_aggregator(url) or is_ignored_host(url) or registered_domain(url) == info["domain"]:
                    continue
                outs.add(url)
            for u in sorted(outs):
                yield {"type": "lead", "lead_name": re.sub(r"\s+", " ", h1)[:200], "found_on_url": response.url,
                       "outbound_url": u, "run_id": self.run_id}
            if not outs:
                yield {"type": "lead", "lead_name": re.sub(r"\s+", " ", h1)[:200], "found_on_url": response.url,
                       "outbound_url": None, "run_id": self.run_id}
        if depth < 2:
            for a in response.css("a[href]"):
                url = urljoin(response.url, a.attrib.get("href", "")).split("#")[0]
                if registered_domain(url) != info["domain"] or url_denied(url):
                    continue
                anchor = " ".join(a.css("::text").getall()).strip()
                if link_score(anchor, url) >= 3:
                    yield from self._req(url, info, depth + 1, priority=5)
