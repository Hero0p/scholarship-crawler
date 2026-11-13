"""Rule-based source classification (PROJECT_PLAN.md §8)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from atlas import config
from atlas.urls import hostname, registered_domain, suffix

OFFICIAL_TYPES = {"GOV_CENTRAL", "GOV_STATE", "REGULATOR", "UNIVERSITY",
                  "CORPORATE_CSR", "FOUNDATION_TRUST", "INTERNATIONAL"}


def is_official_type(source_type: str) -> bool:
    return source_type in OFFICIAL_TYPES


@dataclass
class Classification:
    source_type: str
    trust_tier: int
    is_official: bool
    reason: str


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _in_list(url: str, items: list[str]) -> bool:
    host = hostname(url) or url
    return any(_host_matches(host, d) for d in items)


def is_aggregator(url: str) -> bool:
    return _in_list(url, config.domains()["aggregators"])


def is_news_blog(url: str) -> bool:
    return _in_list(url, config.domains()["news_blogs"])


def is_application_platform(url: str) -> bool:
    return _in_list(url, config.domains()["application_platforms"])


def is_ignored_host(url: str) -> bool:
    return _in_list(url, config.domains()["ignore_hosts"])


def tld_class(url: str) -> str | None:
    """GOV / ACADEMIC by public suffix, else None."""
    sfx = suffix(url)
    d = config.domains()
    if sfx in d["official_gov_suffixes"] or any(sfx.endswith("." + s) for s in d["official_gov_suffixes"]):
        return "GOV"
    if sfx in d["academic_suffixes"]:
        return "ACADEMIC"
    return None


def classify_by_rules(url: str) -> Classification:
    """Classification of an arbitrary URL without registry knowledge."""
    if is_aggregator(url):
        return Classification("AGGREGATOR", 4, False, "known aggregator domain")
    if is_news_blog(url) or re.search(r"/(blog|news|article)s?/", url, re.I):
        return Classification("NEWS_BLOG", 5, False, "news/blog domain or path")
    t = tld_class(url)
    host = hostname(url)
    if t == "GOV":
        if suffix(url).endswith("gov.in") or suffix(url) == "nic.in":
            labels = re.split(r"[.\-]", host)
            states = set(config.domains()["state_tokens"])
            if any(lbl in states for lbl in labels[:-2]):
                return Classification("GOV_STATE", 1, True, f"state government domain ({host})")
            return Classification("GOV_CENTRAL", 1, True, f"Government of India domain ({host})")
        return Classification("INTERNATIONAL", 1, True, f"foreign government domain ({host})")
    if t == "ACADEMIC":
        return Classification("UNIVERSITY", 2, True, f"academic/research domain ({host})")
    return Classification("UNKNOWN", 5, False, "unregistered organisation domain")


def provider_domain_match(provider_or_name: str, url: str) -> float:
    """How well a provider/scholarship name matches a domain label (0-100)."""
    reg = registered_domain(url)
    label = reg.split(".")[0]
    squashed = re.sub(r"[^a-z0-9]", "", (provider_or_name or "").lower())
    if not label or not squashed:
        return 0.0
    # acronym of the name, e.g. "Inlaks Shivdasani Foundation" -> "isf"
    words = re.findall(r"[A-Za-z]+", provider_or_name or "")
    acronym = "".join(w[0] for w in words if w.lower() not in {"of", "the", "and", "for", "in"}).lower()
    # label built from an ordered subset of the name's words, e.g. inlaks+foundation
    generic = {"scholarship", "scholarships", "fellowship", "fellowships", "foundation", "india", "trust",
               "education", "program", "programme", "award", "awards", "the", "for", "and", "of"}
    toks = [w.lower() for w in words if len(w) > 1]
    if label in generic:
        return 0.0
    for i in range(len(toks)):
        for j in range(i, min(len(toks), i + 4)):
            if label == toks[i] + toks[j] or label == "".join(toks[i:j + 1]) or (len(label) >= 6 and label == toks[i]):
                return 100.0
    return max(fuzz.partial_ratio(label, squashed) if len(label) >= 4 else 0.0,
               100.0 if acronym and len(acronym) >= 3 and label.startswith(acronym) else 0.0)


def drift_check(expected_domain: str, aliases: list[str], final_url: str) -> bool:
    """True if the final URL left the expected domain (and its approved aliases)."""
    final = registered_domain(final_url)
    allowed = {expected_domain, *[registered_domain(a) for a in aliases]}
    return final not in allowed
