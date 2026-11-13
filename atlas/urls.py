"""URL canonicalisation and registered-domain helpers."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

# Use the bundled public-suffix snapshot: deterministic, no network fetch.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
             "fbclid", "gclid", "ref", "_ga", "sessionid", "jsessionid", "phpsessid"}


def registered_domain(url_or_host: str) -> str:
    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    if not host:
        return ""
    ext = _EXTRACT(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return host.lower()


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def suffix(url_or_host: str) -> str:
    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    return _EXTRACT(host or "").suffix.lower()


def canonicalize(url: str) -> str:
    """Lower-case scheme/host, drop fragments, tracking params and default ports; sort query."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if not port or port in (80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING]
    query.sort()
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, netloc, path, urlencode(query), ""))


def is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")
