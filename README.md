# Scholarship Intelligence Crawler (Atlas)

An automated, evidence-first crawler that **discovers → classifies → crawls → extracts → verifies → scores →
stores → updates** scholarship opportunities for Indian students, with a Streamlit dashboard.

- **No LLMs anywhere.** Extraction is deterministic: DOM → text blocks → section segmentation → rule-based field
  extractors. The confidence score is a published, weighted-evidence formula with hard gates.
- **Every stored value is traceable:** `scholarships` → `field_evidence` (verbatim snippet + char offsets / PDF page)
  → immutable snapshot (`data/snapshots/<sha256>`) → official source URL.
- **Aggregators are used for discovery only.** Their text is never stored as data.
- **Repeatable runs** detect NEW / UNCHANGED / CHANGED / MISSING records and keep full change history.

See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the design and [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md) for the
methodology write-up.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on Linux/macOS)
pip install -r requirements.txt

python -m atlas.cli init          # create data/atlas.db and load config/sources.yaml
python -m atlas.cli run           # full pipeline (takes ~1–2 h: polite 2 s/request per domain)
python -m atlas.cli stats         # minimum-output checks
streamlit run dashboard/app.py    # dashboard on http://localhost:8501
```

## Commands

| Command | What it does |
|---|---|
| `python -m atlas.cli run [--force]` | recheck known scholarship pages → mine aggregator leads → resolve leads to official domains → focused crawl of official sources → process → enrich → verify → lifecycle → run report. `--force` ignores the recrawl schedule. |
| `python -m atlas.cli report [RUN_ID]` | "Run N: A unchanged · B changed · C missing · D new" |
| `python -m atlas.cli stats` | counts vs. the assignment minimums |
| `python -m atlas.cli wayback --limit 20` | historical baselines: archived official page (Internet Archive) vs current record, stored as `WAYBACK_BASELINE` changes |
| `python -m atlas.cli inspect <url-fragment>` | debug: text, sections, verdict and extracted fields (with evidence) for a stored page |
| `python -m atlas.cli rebuild` | re-derive all records from stored snapshots after improving extractors (no re-crawl) |
| `python -m atlas.cli export` | `data/sample/scholarships.json` + `.csv` (with evidence) |
| `python -m atlas.cli audit --n 20` | random sample + every evidence snippet in a CSV for manual checking |
| `python -m atlas.cli schedule` | runs the pipeline daily at 02:00 IST (APScheduler) |
| `pytest` | unit tests |

## Inspecting the database

`data/atlas.db` is plain SQLite (open it with DB Browser for SQLite or `sqlite3`). Useful queries:

```sql
-- verified records with their official source
SELECT name, provider, confidence, status, closing_date, official_source_url
FROM scholarships WHERE verification_status = 'VERIFIED' ORDER BY confidence DESC;

-- the traceability chain for one record
SELECT field_name, value_json, evidence_text, source_url, snapshot_sha256, locator
FROM field_evidence WHERE scholarship_id = 1 AND is_current = 1;

-- change history (old value, new value, date, source, evidence)
SELECT s.name, c.field_name, c.old_value_json, c.new_value_json, c.detected_at, c.origin,
       c.old_evidence_text, c.new_evidence_text
FROM field_changes c JOIN scholarships s ON s.id = c.scholarship_id ORDER BY c.detected_at DESC;

-- why a score is what it is
SELECT check_name, kind, points, passed, reason FROM verification_checks
WHERE scholarship_id = 1 AND is_current = 1;
```

## Project layout

```
config/            sources.yaml (seed registry) · keywords.yaml · domains.yaml · scoring.yaml · frequency.yaml
atlas/             core library
  extract/         content.py (HTML/PDF→text) · sections.py · fields.py · record.py · cards.py · evidence.py
  classify/        source_classifier.py · page_classifier.py (relevance, soft-404, link scoring)
  verify/          confidence.py (9 signals, 5 gates, penalties)
  lifecycle/       status.py (ACTIVE / EXPIRING_SOON / EXPIRED / REVIEW_REQUIRED / NO_LONGER_VERIFIABLE)
  discovery/       resolver.py (aggregator lead → official domain) · wayback.py
  schedule/        frequency.py (adaptive recrawl)
  process.py       snapshot → record upsert with field-level change detection
  cli.py           Typer CLI
crawler/           Scrapy project (spiders: official, leads, recheck; polite middlewares)
dashboard/app.py   Streamlit UI
tests/             unit tests (synthetic fixtures only)
data/              atlas.db · snapshots/ · sample/
```

## Test-run schedule (how many runs, how often)

| Run | When | What it proves |
|---|---|---|
| Run 1 | 13 Nov 2026, done | Baseline: discovery, extraction, verification. Plus the Wayback historical baseline (47 changes). |
| Run 2 | 13 Nov 2026, done (~2 h later) | Idempotency: a re-run with nothing changed creates **0** false changes |
| Run 3 → Run 8 | **Daily at 02:00 IST**, 14 Nov → ~1 Dec | Live change and stale detection. The NSP deadline is 30-11-2026: records turn ACTIVE → EXPIRING_SOON from 15 Nov, NMMS (closing 15 Nov) turns EXPIRED, and any deadline extension shows up as a CHANGED event. |

Minimum for a credible test: **5 runs** (baseline + repeat + 3 daily). Recommended: daily until the day after the
30 Nov NSP deadline. Keep the interval at **24 h**. The adaptive policy already re-checks pages near a deadline every
12 h, so running the full pipeline more often only adds load on government servers.

Register the daily run with Windows Task Scheduler (the PC must be on at 02:00):

```bat
schtasks /Create /SC DAILY /ST 02:00 /TN "AtlasScholarshipCrawler" /TR "\"D:\Coding\Scholarship Crawler\scripts\run_daily.bat\""
```

## Automation

- Locally: `python -m atlas.cli schedule` (daily 02:00 IST) or Windows Task Scheduler / cron calling
  `python -m atlas.cli run --trigger SCHEDULED`.
- GitHub Actions: `.github/workflows/crawl.yml` runs daily and commits the updated DB + evidence.
- Live dashboard: deploy `dashboard/app.py` on Streamlit Community Cloud (free) pointing at the repo.

## Crawling etiquette

robots.txt obeyed · honest User-Agent · ≤ 2 concurrent requests per domain with a ~2 s randomised delay and
AutoThrottle · conditional GETs (ETag / Last-Modified) · non-blocking Retry-After · per-domain circuit
breaker (5 consecutive 403/429/5xx → domain paused 24 h) · no proxies, no UA spoofing, no CAPTCHA solving,
no login-gated pages.
