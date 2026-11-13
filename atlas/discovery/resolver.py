"""Resolve aggregator leads to OFFICIAL sources (aggregator content itself is never stored as data).

Rules (PROJECT_PLAN.md §7.2):
 1. outbound link to an official-TLD domain (gov.in, nic.in, ac.in, res.in, edu, gov.uk ...)
      -> new source, auto-APPROVED, crawled from that exact URL (origin DISCOVERED_AGGREGATOR)
 2. outbound link to an organisation domain whose label matches the provider/scholarship name
      -> new source crawled, but typed PROVIDER_UNAPPROVED (S1 = 0.6 => can never be VERIFIED
         until a human approves the domain in config/sources.yaml)
 3. lead name matches an existing official record -> MATCHED_EXISTING (corroborating discovery)
 4. otherwise UNRESOLVED (shown in the dashboard, never becomes a record)
"""
from __future__ import annotations

import json
import re

from rapidfuzz import fuzz

from atlas.classify.source_classifier import (classify_by_rules, is_aggregator, is_ignored_host,
                                              is_news_blog, provider_domain_match, tld_class)
from atlas.db.models import DiscoveryLead, Scholarship, Source
from atlas.db.session import get_session
from atlas.urls import registered_domain

MAX_NEW_SOURCES_PER_RUN = 40
LEAD_BUDGET = 8
SKIP_PATH = re.compile(r"/(privacy|terms|contact|about|login|signin|careers?)\b", re.I)


def resolve_leads(run_id=None) -> dict:
    stats = {"RESOLVED": 0, "PROVIDER_MATCH": 0, "MATCHED_EXISTING": 0, "UNRESOLVED": 0, "new_sources": 0}
    with get_session() as s:
        known = {src.registered_domain: src for src in s.query(Source)}
        names = [(sch.id, sch.name.lower()) for sch in s.query(Scholarship)]
        leads = s.query(DiscoveryLead).filter(DiscoveryLead.status == "NEW").all()
        by_page: dict[str, list[DiscoveryLead]] = {}
        for ld in leads:
            by_page.setdefault(ld.found_on_url, []).append(ld)
        new_sources = 0
        for page_url, group in by_page.items():
            name = group[0].lead_name or ""
            resolved = False
            for ld in group:
                url = ld.outbound_url
                if not url or is_ignored_host(url) or is_aggregator(url) or is_news_blog(url) or SKIP_PATH.search(url):
                    continue
                dom = registered_domain(url)
                official_tld = tld_class(url) is not None
                match = provider_domain_match(name, url)
                if not official_tld and match < 85:
                    continue
                ld.resolved_official_url = url
                ld.resolution_method = "OUTLINK_OFFICIAL_TLD" if official_tld else "OUTLINK_PROVIDER_MATCH"
                ld.status = "RESOLVED"
                resolved = True
                stats["RESOLVED" if official_tld else "PROVIDER_MATCH"] += 1
                src = known.get(dom)
                if src is None and new_sources < MAX_NEW_SOURCES_PER_RUN:
                    cls = classify_by_rules(url)
                    src = Source(registry_key=f"lead:{dom}", name=dom, provider_name=None, registered_domain=dom,
                                 domain_aliases="[]", seeds=json.dumps([url]), sitemaps="[]",
                                 source_type=cls.source_type if official_tld else "PROVIDER_UNAPPROVED",
                                 trust_tier=cls.trust_tier if official_tld else 3,
                                 is_official=bool(official_tld and cls.is_official), role="CRAWL",
                                 origin="DISCOVERED_AGGREGATOR", approval_status="APPROVED",
                                 approval_reason=(f"auto: {cls.reason}" if official_tld else
                                                  f"provider-name/domain match {match:.0f} (needs human approval to be official)")
                                 + f"; lead '{name[:80]}' on {page_url}",
                                 max_depth=1, page_budget=LEAD_BUDGET, base_frequency_hours=168,
                                 discovered_from=page_url)
                    s.add(src)
                    s.flush()
                    known[dom] = src
                    new_sources += 1
                    stats["new_sources"] += 1
                elif src is not None and url not in json.loads(src.seeds or "[]") and src.origin != "SEED":
                    seeds = json.loads(src.seeds or "[]")
                    if len(seeds) < 5:
                        src.seeds = json.dumps(seeds + [url])
                break
            if not resolved:
                nm = name.lower()
                hit = next((sid for sid, n in names if fuzz.token_set_ratio(n, nm) >= 92), None) if nm else None
                for ld in group:
                    if hit:
                        ld.status, ld.resolution_method = "MATCHED_EXISTING", "NAME_MATCH"
                        ld.note = f"matches scholarship #{hit}"
                    else:
                        ld.status, ld.resolution_method = "UNRESOLVED", "UNRESOLVED"
                stats["MATCHED_EXISTING" if hit else "UNRESOLVED"] += 1
            else:
                for ld in group:
                    if ld.status == "NEW":
                        ld.status = "SUPERSEDED"
        s.commit()
    return stats
