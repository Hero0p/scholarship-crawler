"""Scrapy settings: polite-by-design crawling (PROJECT_PLAN.md §15)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas import config  # noqa: E402

BOT_NAME = "atlas_crawler"
SPIDER_MODULES = ["atlas_crawler.spiders"]
NEWSPIDER_MODULE = "atlas_crawler.spiders"

USER_AGENT = config.USER_AGENT
ROBOTSTXT_OBEY = True
ROBOTSTXT_USER_AGENT = "AtlasScholarshipBot"

CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 2.0
DOWNLOAD_DELAY_JITTER = 0.5          # +/-50% randomised politeness delay
MEMUSAGE_ENABLED = False
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 60
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0

DOWNLOAD_TIMEOUT = 45
DOWNLOAD_MAXSIZE = 25 * 1024 * 1024
DOWNLOAD_WARNSIZE = 10 * 1024 * 1024
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504, 522, 524]
REDIRECT_MAX_TIMES = 8
DEPTH_LIMIT = 5
COOKIES_ENABLED = True
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.6",
}
CLOSESPIDER_PAGECOUNT = 6000          # hard safety budget per spider run
LOG_LEVEL = "INFO"

DOWNLOADER_MIDDLEWARES = {
    "atlas_crawler.middlewares.ConditionalGetMiddleware": 540,
    "atlas_crawler.middlewares.DomainCircuitBreakerMiddleware": 545,
    "atlas_crawler.middlewares.RetryAfterMiddleware": 549,
}
ITEM_PIPELINES = {
    "atlas_crawler.pipelines.PersistPipeline": 200,
}

HTTPCACHE_ENABLED = False
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
FEED_EXPORT_ENCODING = "utf-8"

# Optional JS rendering (only for sources flagged render_js: true) via scrapy-playwright.
try:  # pragma: no cover - depends on optional install
    import scrapy_playwright  # noqa: F401
    PLAYWRIGHT_AVAILABLE = True
    DOWNLOAD_HANDLERS = {
        "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    }
    TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
    PLAYWRIGHT_BROWSER_TYPE = "chromium"
    PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}
    PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = 45000
    PLAYWRIGHT_ABORT_REQUEST = "atlas_crawler.middlewares.abort_heavy_resources"
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
