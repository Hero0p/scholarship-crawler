# Scholarship Intelligence Crawler — Project Plan

> **Goal:** A working, automated system that **discovers → classifies → crawls → extracts → verifies → scores → stores → updates** authentic scholarship opportunities for Indian students, and shows them in a Streamlit dashboard.
>
> **Hard constraints (our choices + assignment rules):** no LLMs anywhere · Scrapy for crawling · Streamlit for the UI · free/open-source tools only · every stored value must trace back to official-source evidence.

---

## 0. Key decisions at a glance

| Topic | Decision |
|---|---|
| Crawling strategy | **Hybrid.** Curated **URL seeding** of official hubs, then a **bounded, focused deep crawl** inside each official domain. Add **sitemap expansion**, **aggregator lead-mining** (for discovery only) and **outlink expansion** to newly found official domains. Pure seeding fails the "not hard-coded" rule. A pure deep crawl wastes budget on government sites full of tenders and RTI pages, and gets you blocked. |
| Extraction | Rule-based information extraction. Main-content cleaning, then section segmentation by heading synonyms, then field-specific regex/dictionary extractors. Every value is stored **with its verbatim evidence snippet**. |
| Confidence score | Deterministic weighted-evidence model: **9 signals (100 points) − conflict penalties**, plus **5 hard gates**. VERIFIED only if score is **≥ 95** *and* all gates pass. Each record has a "Why this score?" breakdown. |
| Anti-hallucination | No generative models. **Null over guess**: missing means "Not specified". A field value can't be written without an evidence row. Every snippet is re-checked against the stored snapshot. |
| Change detection | 4 levels: HTTP 304, then content hash, then field-level diff, then record events. Old values are **never overwritten**. Changes go to `field_changes` and `scholarship_versions`. |
| Frequency | Adaptive revisit. Base interval by source type, adjusted by deadline proximity and change-rate backoff. Crawls run in the off-peak IST window. |
| Anti-blocking | Polite-by-design: obey robots.txt, use an honest User-Agent, enable AutoThrottle, limit to 1–2 requests per domain at a time, send conditional GETs, add a per-domain circuit breaker. **No** proxy rotation, UA spoofing or CAPTCHA solving. |
| Storage | **SQLite** through SQLAlchemy, so we can swap to Postgres or Supabase with no code changes. Raw page snapshots are stored by SHA-256 as evidence. |
| Automation | GitHub Actions cron (free) for the live demo. Locally, APScheduler or Windows Task Scheduler. |
| UI | Multi-page Streamlit app: KPIs, searchable list, detail view with score explanation, change history and evidence, crawl-run log, discovery queue. |

---

## 1. Understanding the assignment

### 1.1 What must be built (summary of the brief)
1. **Pipeline:** Discovers → Crawls → Extracts → Verifies → Scores → Stores → Updates, for scholarships open to **Indian students**.
2. **Sources:** government portals, central and state departments, UGC/AICTE/ministries, universities, corporate CSR, foundations and trusts, international, and other legitimate providers.
3. **Authenticity first.** Accuracy matters more than quantity. Each record keeps its **official/primary source**. Aggregators, blogs and articles are for **discovery only**.
4. **Each verified record must have:** official source URL, application URL (where available), source type, date last verified, and the evidence used.
5. **Schema (minimum):** name, provider, official source, application URL, amount/benefit, eligibility, academic requirements, course/education level, income, age, gender, category, domicile/state, institution requirements, opening date, closing date, documents, selection process, renewal, and current status. Add Atlas-style machine-readable criteria: disability, defence, sports, research.
6. **Verification engine:** an evidence-based confidence score. **Do not ask an LLM for a number.** **VERIFIED if ≥ 95%**, otherwise **REVIEW_REQUIRED**.
7. **No hallucination:** unknown fields show **"Not specified"**. Every important field must be traceable: **Database → Official Source → Evidence → Extracted Value**.
8. **Auto crawler:** repeatable runs that detect new, unchanged, changed and removed scholarships.
9. **Change detection:** keep old value, new value, date detected, source and evidence. Never just overwrite.
10. **Stale/expired:** statuses `ACTIVE`, `EXPIRING_SOON`, `EXPIRED`, `REVIEW_REQUIRED`, `NO_LONGER_VERIFIABLE`.
11. **Free database** that we can inspect, holding the real records.
12. **Simple UI:** KPIs (total, verified, review required, active, expired, recently updated, average confidence), a searchable list, and a detail view (all fields, confidence, *why this score*, last verified, change history, source evidence).
13. **Discovery must not be fully hard-coded.** Show Discovery → Source classification → Crawling → Extraction → Verification, and find scholarships beyond the seed list.
14. **Free tools only.** No paid APIs or scraping services.

### 1.2 Minimum output and how we hit it

| Requirement | Our target | How we prove it (SQL in demo) |
|---|---|---|
| 20+ real scholarships | **40–60** | `SELECT COUNT(*) FROM scholarships;` |
| 15+ verified against primary/official sources | **30+** | `… WHERE official_source_tier <= 3` |
| 10+ with confidence ≥ 95% | **15+** | `… WHERE confidence >= 95` |
| ≥ 3 source types | **6** (central govt, state govt, university, CSR, foundation/trust, international) | `SELECT source_type, COUNT(*) … GROUP BY 1` |
| ≥ 2 change-detection examples | **3+ real** (live re-crawls in peak season, plus Wayback historical baselines, clearly labelled) | `SELECT * FROM field_changes;` |
| ≥ 2 expired/stale examples | **4+** (past deadlines, removed pages, lapsed domains) | `… WHERE status IN ('EXPIRED','NO_LONGER_VERIFIABLE')` |

### 1.3 Evaluation weights mapped to our effort

| Criterion | Weight | Where we invest |
|---|---|---|
| Authenticity & official-source verification | 25% | Source classifier, official-domain registry, domain-drift check, gates G1/G2 |
| Working crawler & automation | 20% | Scrapy pipeline, scheduler, adaptive recrawl, GitHub Actions |
| Accuracy of extracted information | 20% | Section-scoped extractors, validators, golden-set audit |
| Confidence / verification engine | 15% | 9-signal model, gates, explanations, calibration |
| Change & stale-data detection | 10% | Field diff, versions, lifecycle engine |
| Working UI | 5% | Streamlit dashboard |
| Code quality | 5% | Typed modules, tests, config-driven design |

### 1.4 Automatic-failure triggers and how we avoid them

| Failure trigger | Mitigation |
|---|---|
| Only a design doc or a UI mockup | Working crawler first. The dashboard reads the real DB. |
| Crawler doesn't actually work | Daily scheduled runs from Day 2. Run logs are visible in the UI. |
| Fabricated information | No generative models. An evidence row is required per field. Verbatim snippet check. Null over guess. |
| Official sources can't be traced | `official_source_url` + `snapshot_sha256` + `evidence_text` + `locator` per field |
| Arbitrary confidence | Published formula in `config/scoring.yaml`, with a per-signal breakdown stored for each record |
| Copying from aggregators | Aggregator text never becomes a field value. It only produces `discovery_leads` that must resolve to an official page. |
| Paid services | Everything open-source or free-tier |

---

## 2. Guiding principles

1. **Evidence or nothing.** A field is written only together with `(source_url, snapshot_sha256, verbatim_snippet, locator)`.
2. **Null over guess.** Missing means `NULL`, shown as "Not specified". No cross-field inference (for example, never infer an income cap from a category).
3. **Official beats everything.** Aggregators and blogs → leads only. Conflicts between an official source and an aggregator are logged and ignored.
4. **Fetch once, process many times.** Crawling stores immutable snapshots. Extraction and verification re-run from those snapshots, so improving an extractor never needs a re-crawl.
5. **Deterministic and explainable.** Same inputs, same score. Every number has a human-readable reason.
6. **Config over code.** Sources, keywords, heading synonyms, weights and frequencies all live in YAML.
7. **Polite crawler.** We are a guest on public-sector infrastructure.

---

## 3. Architecture

```
                 ┌────────────────────────────┐
                 │ config/sources.yaml (seeds)│◀──── approved discovered domains
                 └─────────────┬──────────────┘
┌───────────────┐              ▼
│  Scheduler    │   ┌──────────────────────────────────────────────────────────┐
│ (GH Actions / │──▶│ 1. DISCOVERY                                             │
│  APScheduler) │   │  seed hubs · sitemaps · focused outlinks ·               │
└───────────────┘   │  aggregator leads → official-URL resolution · Wikidata   │
                    └─────────────┬────────────────────────────────────────────┘
                                  ▼
                    2. SOURCE CLASSIFICATION   domain rules + registry + drift check
                                  ▼
                    3. CRAWLING (Scrapy)       polite · conditional GET · PDF ·
                                  │            Playwright only for JS-flagged sources
                                  ▼            raw snapshot → data/snapshots/<sha256>
                    4. FILTERING               soft-404 · relevance score · listing vs detail ·
                                  │            India-eligibility · dedup / merge
                                  ▼
                    5. EXTRACTION              clean text → sections → field extractors
                                  │            → value + verbatim evidence + locator
                                  ▼
                    6. VERIFICATION            9 signals · gates · conflict penalties
                                  │            → confidence + VERIFIED / REVIEW_REQUIRED
                                  ▼
                    7. CHANGE & LIFECYCLE      field diff · versions · run events · statuses
                                  ▼
                    8. STORAGE (SQLite)        records · evidence · changes · runs · leads
                                  ▼
                    9. STREAMLIT DASHBOARD     read-only views over the DB
```

**Run orchestration (`atlas run`):**
`crawl` (Scrapy: discover + recheck), then `process` (filter + extract from new snapshots), then `verify` (score all touched records, which needs every source for corroboration), then `lifecycle` (statuses, change events), then `report` (run summary).

---

## 4. Tech stack and dependencies

### 4.1 Stack

| Layer | Tool | Why |
|---|---|---|
| Language | **Python 3.11+** (3.12 recommended; 3.13 is installed locally and should work, pin versions if a wheel is missing) | Ecosystem |
| Crawling | **Scrapy** | Mature scheduler, AutoThrottle, robots.txt (Protego), retries, HTTP cache, item pipelines, sitemap spider |
| JS rendering (selective) | **scrapy-playwright** + Playwright Chromium | Only for SPA sources (myScheme, Reliance Foundation). Recent releases support Windows. If event-loop issues appear, run those sources under WSL or in the Linux CI runner. |
| HTML parsing | **lxml**, **parsel** (bundled with Scrapy), **BeautifulSoup4** | DOM walking for section segmentation |
| Main-content extraction | **trafilatura** | Removes nav and boilerplate, keeps tables |
| PDF | **pdfplumber** | Text and tables with page numbers (used as evidence locators) |
| Dates | **dateparser** + **python-dateutil** | Strict DMY parsing of Indian date formats |
| Fuzzy matching | **rapidfuzz** | Name matching, heading synonyms, dedup |
| Domains/URLs | **tldextract**, **w3lib** | Registered domain, canonical URLs |
| Validation | **pydantic v2** | Typed records, range validators |
| DB | **SQLite** (+ FTS5 for search) via **SQLAlchemy 2.x** | Free, inspectable single file. Postgres/Supabase-ready. |
| Config | **PyYAML** | Sources, keywords, weights |
| Scheduling | **GitHub Actions cron** (live) · **APScheduler** (local daemon) | Free |
| CLI | **Typer** | `atlas crawl / process / verify / run / export` |
| Dashboard | **Streamlit** + **pandas** + **plotly** | Simple UI, free hosting on Streamlit Community Cloud |
| External free APIs | Internet Archive **Wayback CDX**, **Wikidata** (P856 "official website") | Historical baselines; resolving provider → official domain |
| Optional ML (non-LLM) | **scikit-learn** (TF-IDF + Logistic Regression) | Second opinion for the page classifier, once a labelled set exists |
| Optional OCR | **pytesseract** + Tesseract binary | Scanned PDF notices only |
| Testing / quality | **pytest**, **ruff** | |

### 4.2 `requirements.txt` (starting point)

```txt
# --- crawling ---
scrapy>=2.11
scrapy-playwright>=0.0.40      # only used for render_js sources
playwright>=1.45               # then: playwright install chromium
# --- parsing & extraction ---
lxml>=5.2
beautifulsoup4>=4.12
trafilatura>=1.12
pdfplumber>=0.11
dateparser>=1.2
python-dateutil>=2.9
rapidfuzz>=3.9
tldextract>=5.1
# --- data, config, validation ---
sqlalchemy>=2.0
pydantic>=2.7
pyyaml>=6.0
requests>=2.32                 # Wayback CDX + Wikidata lookups
# --- orchestration ---
typer>=0.12
apscheduler>=3.10
# --- dashboard ---
streamlit>=1.36
pandas>=2.2
plotly>=5.22
# --- optional ---
# scikit-learn>=1.5
# pytesseract>=0.3
# --- dev ---
pytest>=8.2
ruff>=0.5
```

**System dependencies:** Playwright Chromium (`playwright install chromium`). Optional: Tesseract OCR binary.

---

## 5. Project structure

```
scholarship-crawler/
├── README.md                      # setup, run, demo, architecture summary
├── PROJECT_PLAN.md                # this file
├── TECHNICAL_NOTE.md              # ≤ 3 pages (submission item D)
├── requirements.txt
├── .env.example                   # DB_URL, USER_AGENT contact URL, etc.
├── config/
│   ├── sources.yaml               # seed registry (see §15)
│   ├── domains.yaml               # official TLD rules, aggregator/news lists, aliases
│   ├── keywords.yaml              # relevance terms, deny terms, heading synonyms (EN + HI)
│   ├── scoring.yaml               # signal weights, gates, thresholds (versioned)
│   └── frequency.yaml             # revisit policy
├── atlas/                         # core library (framework-independent)
│   ├── cli.py                     # Typer entrypoints
│   ├── db/  models.py  repository.py  session.py
│   ├── classify/  source_classifier.py  page_classifier.py  soft404.py
│   ├── extract/
│   │   ├── content.py             # trafilatura/pdfplumber → text + offset map
│   │   ├── sections.py            # heading → canonical section
│   │   ├── fields/  name.py provider.py amount.py dates.py income.py age.py
│   │   │            gender.py category.py domicile.py education.py academic.py
│   │   │            documents.py application.py selection.py renewal.py
│   │   └── evidence.py            # ExtractedField, verbatim check
│   ├── verify/  signals.py  gates.py  confidence.py  corroboration.py
│   ├── lifecycle/  diff.py  status.py  events.py
│   ├── discovery/  aggregator_leads.py  resolver.py  wayback.py  wikidata.py
│   └── schedule/  frequency.py  runner.py
├── crawler/                       # Scrapy project
│   ├── scrapy.cfg
│   └── atlas_crawler/
│       ├── settings.py
│       ├── items.py               # PageItem, LeadItem
│       ├── middlewares.py         # ConditionalGet, DomainCircuitBreaker, SoftRetryAfter
│       ├── pipelines.py           # SnapshotPipeline → PagePersistPipeline → LeadPipeline
│       └── spiders/
│           ├── official.py        # seeded focused crawl over approved sources
│           ├── sitemaps.py        # SitemapSpider with scholarship rules + lastmod filter
│           ├── leads.py           # aggregator lead mining (discovery only)
│           └── recheck.py         # revisits due URLs from DB (change detection)
├── dashboard/
│   ├── app.py                     # Overview
│   └── pages/ 1_Scholarships.py 2_Changes.py 3_Crawl_Runs.py 4_Sources_and_Discovery.py
├── data/
│   ├── atlas.db                   # SQLite (committed snapshot for demo)
│   ├── snapshots/                 # <sha256>.html.gz / .pdf / .txt.gz
│   └── sample/ scholarships.json scholarships.csv
├── scripts/  export_sample.py  audit_sample.py  seed_registry.py
├── tests/
│   ├── fixtures/                  # saved real pages (HTML/PDF) + v1/v2 synthetic test pages
│   ├── test_extractors.py  test_confidence.py  test_lifecycle.py  test_soft404.py
│   └── test_invariants.py         # "every non-null field has evidence"
└── .github/workflows/crawl.yml    # daily cron
```

---

## 6. Data model (SQLite)

```sql
-- Who publishes what, and how much we trust them
CREATE TABLE sources (
  id                  INTEGER PRIMARY KEY,
  registry_key        TEXT UNIQUE,
  name                TEXT NOT NULL,
  provider_name       TEXT,
  registered_domain   TEXT NOT NULL,            -- tldextract, e.g. aicte.gov.in
  domain_aliases      TEXT,                     -- JSON, approved redirects/rebrands
  source_type         TEXT NOT NULL,            -- GOV_CENTRAL|GOV_STATE|REGULATOR|UNIVERSITY|
                                                -- CORPORATE_CSR|FOUNDATION_TRUST|INTERNATIONAL|
                                                -- APPLICATION_PLATFORM|AGGREGATOR|NEWS_BLOG|UNKNOWN
  trust_tier          INTEGER NOT NULL,         -- 1 (govt) … 5 (untrusted)
  is_official         BOOLEAN NOT NULL,
  origin              TEXT NOT NULL,            -- SEED|DISCOVERED_OUTLINK|DISCOVERED_AGGREGATOR|DISCOVERED_WIKIDATA
  approval_status     TEXT NOT NULL,            -- APPROVED|PENDING|REJECTED|BROKEN|BLOCKED
  render_js           BOOLEAN DEFAULT 0,
  base_frequency_hours INTEGER,
  page_budget         INTEGER, max_depth INTEGER,
  paused_until        TIMESTAMP,
  consecutive_failures INTEGER DEFAULT 0,
  last_crawled_at     TIMESTAMP, created_at TIMESTAMP
);

CREATE TABLE crawl_runs (
  id INTEGER PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
  trigger TEXT, mode TEXT,                       -- SCHEDULED|MANUAL ; DISCOVER|RECHECK|FULL
  pages_fetched INT, pages_not_modified INT, errors INT,
  new_count INT, changed_count INT, unchanged_count INT, missing_count INT, expired_count INT,
  stats_json TEXT
);

CREATE TABLE pages (
  id INTEGER PRIMARY KEY,
  canonical_url TEXT UNIQUE NOT NULL, url TEXT NOT NULL, final_url TEXT,
  source_id INTEGER REFERENCES sources(id),
  content_type TEXT,
  page_kind TEXT,                                -- DETAIL|LISTING|PDF_NOTICE|IRRELEVANT|SOFT_404
  relevance_score REAL, depth INTEGER, discovered_from_page_id INTEGER,
  http_status INTEGER, etag TEXT, last_modified TEXT,
  content_hash TEXT,                             -- sha256 of normalised main text
  latest_snapshot_sha TEXT,
  first_seen_at TIMESTAMP, last_fetched_at TIMESTAMP, last_changed_at TIMESTAMP,
  next_crawl_at TIMESTAMP, unchanged_streak INT DEFAULT 0, consecutive_failures INT DEFAULT 0
);

CREATE TABLE snapshots (
  sha256 TEXT PRIMARY KEY, page_id INTEGER, fetched_at TIMESTAMP, http_status INT,
  raw_path TEXT, text_path TEXT,
  origin TEXT NOT NULL DEFAULT 'LIVE',           -- LIVE|WAYBACK|TEST_FIXTURE
  archive_url TEXT
);

-- Current state of each scholarship (one row per scholarship entity)
CREATE TABLE scholarships (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT UNIQUE NOT NULL,              -- norm(name minus year) + provider domain
  name TEXT NOT NULL, provider TEXT, provider_type TEXT,
  funding_type TEXT,                             -- SCHOLARSHIP|FELLOWSHIP|GRANT|AWARD|FEE_WAIVER
  official_source_url TEXT NOT NULL, official_source_tier INT, source_type TEXT NOT NULL,
  application_url TEXT, application_mode TEXT,   -- ONLINE|OFFLINE|NULL
  amount_text TEXT, amount_min_inr INT, amount_max_inr INT, amount_period TEXT, non_monetary_benefits TEXT,
  eligibility_text TEXT,
  education_levels TEXT, courses TEXT,           -- JSON arrays
  min_percentage REAL, min_cgpa REAL, academic_text TEXT,
  income_limit_inr INT, income_text TEXT,
  age_min INT, age_max INT,
  gender TEXT,                                   -- FEMALE|MALE|TRANSGENDER|ALL|NULL
  categories TEXT,                               -- JSON: SC,ST,OBC,EWS,MINORITY,PWD,DEFENCE,SPORTS,ORPHAN,...
  disability_min_percent INT, domicile_states TEXT, institution_text TEXT,
  opening_date DATE, closing_date DATE, deadline_text TEXT, is_rolling BOOLEAN, academic_year TEXT,
  documents_required TEXT, selection_process TEXT, renewal_text TEXT,
  status TEXT NOT NULL,                          -- ACTIVE|EXPIRING_SOON|EXPIRED|REVIEW_REQUIRED|NO_LONGER_VERIFIABLE
  verification_status TEXT NOT NULL,             -- VERIFIED|REVIEW_REQUIRED
  confidence REAL NOT NULL, confidence_breakdown TEXT, scoring_version TEXT,
  first_seen_at TIMESTAMP, last_verified_at TIMESTAMP, last_changed_at TIMESTAMP,
  version INT DEFAULT 1, consecutive_misses INT DEFAULT 0
);

CREATE TABLE scholarship_sources (               -- multi-source support (corroboration)
  scholarship_id INT, page_id INT,
  role TEXT,                                     -- PRIMARY|CORROBORATING|APPLICATION|DISCOVERY
  is_official BOOLEAN, first_seen_at TIMESTAMP, last_seen_at TIMESTAMP,
  PRIMARY KEY (scholarship_id, page_id)
);

-- The traceability chain: DB → source → evidence → value
CREATE TABLE field_evidence (
  id INTEGER PRIMARY KEY,
  scholarship_id INT NOT NULL, field_name TEXT NOT NULL, value_json TEXT NOT NULL,
  evidence_text TEXT NOT NULL,                   -- verbatim snippet (sentence/row)
  source_url TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
  locator TEXT,                                  -- {"char":[s,e]} | {"xpath":..} | {"pdf_page":3}
  section TEXT, extractor TEXT, extraction_pass TEXT,  -- SECTION|FULLTEXT
  run_id INT, captured_at TIMESTAMP, is_current BOOLEAN DEFAULT 1
);

CREATE TABLE field_changes (
  id INTEGER PRIMARY KEY, scholarship_id INT, field_name TEXT,
  old_value_json TEXT, new_value_json TEXT, change_kind TEXT,   -- UPDATED|ADDED|REMOVED
  detected_at TIMESTAMP, run_id INT, source_url TEXT,
  old_evidence_id INT, new_evidence_id INT
);

CREATE TABLE scholarship_versions (
  scholarship_id INT, version INT, record_json TEXT, run_id INT, created_at TIMESTAMP,
  PRIMARY KEY (scholarship_id, version)
);

CREATE TABLE verification_checks (               -- "Why this score?"
  id INTEGER PRIMARY KEY, scholarship_id INT, run_id INT,
  check_name TEXT, kind TEXT,                    -- SIGNAL|GATE|PENALTY
  weight REAL, value REAL, points REAL, passed BOOLEAN, reason TEXT
);

CREATE TABLE run_events (
  id INTEGER PRIMARY KEY, run_id INT, scholarship_id INT,
  event TEXT,                                    -- NEW|UNCHANGED|CHANGED|MISSING|REAPPEARED|EXPIRED|STATUS_CHANGED
  detail_json TEXT, created_at TIMESTAMP
);

CREATE TABLE discovery_leads (
  id INTEGER PRIMARY KEY, lead_name TEXT, found_on_url TEXT, aggregator_domain TEXT,
  outbound_url TEXT, resolved_official_url TEXT,
  resolution_method TEXT,                        -- OUTLINK|WIKIDATA|NAME_MATCH|UNRESOLVED
  status TEXT,                                   -- NEW|RESOLVED|MATCHED_EXISTING|REJECTED
  run_id INT, created_at TIMESTAMP
);

CREATE VIRTUAL TABLE scholarships_fts USING fts5(
  name, provider, eligibility_text, content='scholarships', content_rowid='id'
);
```

**Invariant (enforced in `repository.py` and tested):** for every non-null scholarship field there is at least one `field_evidence` row with `is_current = 1`. Its `evidence_text` must appear verbatim in the snapshot text identified by `snapshot_sha256`.

---

## 7. Crawling strategy — URL seeding, deep crawl, or both?

### 7.1 Verdict: **both, as a hybrid "seeded focused crawl"**

| Approach | Strength | Weakness | Role in our system |
|---|---|---|---|
| URL seeding only | High precision, authoritative | Fails the "don't hard-code" rule, misses new schemes | **Entry points only** (~50 official hubs) |
| Unbounded deep crawl | High recall | Government sites are huge (tenders, RTI, circulars, galleries). Wastes budget, raises block risk, low precision. | ✗ |
| **Bounded focused deep crawl** from seeds | Finds scheme pages and PDFs the seed list doesn't name | Needs good link scoring | **Main crawler** |
| Sitemap expansion | Cheap, complete URL lists, `lastmod` | Not every site has one (myScheme's sitemap lists only 38 URLs) | Where available (e.g., Buddy4Study for leads) |
| Aggregator lead mining | Finds CSR/foundation schemes we don't know about | Not authoritative | **Discovery only.** Must resolve to the official page. |
| Outlink expansion | Finds new official domains (ministry → state portal → university) | Domain sprawl | New official-TLD domains enter as `PENDING` with a 50-page budget |

### 7.2 Discovery flow

```
Seeds (sources.yaml, APPROVED)
  │
  ├─▶ Focused crawl inside registered domain (depth ≤ 3; hubs ≤ 4; budget 200–300 pages/domain)
  │      └─ off-domain link to *.gov.in / *.nic.in / *.ac.in / *.edu / *.res.in?
  │             → new source (origin=DISCOVERED_OUTLINK, approval=PENDING → auto-approve by rules)
  │
  ├─▶ Sitemaps (robots.txt `Sitemap:` + /sitemap.xml) → filter by scholarship URL rules + lastmod
  │
  └─▶ Aggregator lead mining (listing pages + sitemaps of aggregators)
         → lead {name, aggregator URL, outbound "official website"/"apply" links}
         → resolver:
              1. outbound link to official-classified domain? → RESOLVED
              2. fuzzy name match to an existing official record? → MATCHED_EXISTING (+ corroboration)
              3. provider org → Wikidata P856 official website → crawl that domain for the name
              4. otherwise UNRESOLVED → shown in Discovery tab, never becomes a record
```

**Auto-approval rules for discovered domains:** official government/academic TLD (`gov.in`, `nic.in`, `ac.in`, `edu.in`, `res.in`, `edu`, `gov.uk`, `go.jp`, …) **and** the scholarship name is found on a page of that domain. Organisation (`.org`/`.com`) domains need the provider name to match the domain or page header (rapidfuzz ≥ 85) **and** a backlink or Wikidata P856 match. Anything else stays `PENDING` for manual approval in the dashboard.

### 7.3 Focused-crawl link scoring

```
link_score = 3·[strong term in anchor text]
           + 2·[strong term in URL path]
           + 1·[weak term in anchor/URL]
           + 1·[link sits under a nav/section titled "Scholarships"/"Schemes"/"Students"]
           + 1·[.pdf AND anchor contains strong term or "guidelines"/"notification"/"advertisement"]
           − 5·[deny term in anchor/URL]

follow   if link_score ≥ 2   (or depth ≤ 1 on a hub seed, to reach "Schemes" menus)
priority = 10·link_score − depth          # Scrapy Request.priority → best-first crawl
```

- **Strong terms:** scholarship, fellowship, stipend, bursary, chhatravritti, छात्रवृत्ति, pre-matric, post-matric, merit-cum-means, "financial assistance", "free-ship"/freeship.
- **Weak terms:** scheme, yojana, योजना, award, grant, students, education, merit, girl child, minority, SC/ST/OBC.
- **Deny terms:** tender, recruitment, vacancy, career(s), job, result, merit-list (as a page type), answer-key, admit-card, gallery, photo, video, RTI, press-release, newsletter, annual-report, budget, minutes, faculty, login, print, share, social links, language duplicates (`?lang=hi` when an English version exists).

### 7.4 Special page handling

| Situation | Handling |
|---|---|
| **PDF notices/guidelines** (very common on government sites) | Follow at depth+1 if relevant. Extract with pdfplumber. Locator = PDF page number. Scanned PDFs (no text layer) → `NEEDS_OCR` → optional pytesseract, else REVIEW. |
| **JS-rendered SPAs** (myScheme, Reliance Foundation) | `render_js: true` in the registry → scrapy-playwright for that source only. Prefer the public JSON endpoints the page itself calls, if visible in network traffic. |
| **ASP.NET postback sites** (e.g., Bihar PMS, Tribal Affairs) | `FormRequest.from_response` for `__VIEWSTATE` pagination. Never submit login forms. |
| **Bilingual / Hindi-first sites** (tribal.nic.in redirects to a Hindi home page) | Prefer the English path. Hindi keywords are in `keywords.yaml`. Dates and amounts are language-independent regex. Hindi-only detail pages → extract what's safe, then REVIEW. |
| **Broken TLS chains** (seen on ksb.gov.in, online-inspire.gov.in, ongcscholar.org) | Scrapy doesn't enforce certificate verification by default, so these still load. Record `tls_valid=false` for information only; don't penalise. |

---

## 8. Source classification (rule-based)

Applied to the **final URL after redirects**:

| Class | Rule | Tier | Official? |
|---|---|---|---|
| `GOV_CENTRAL` | `*.gov.in`, `*.nic.in` (non-state) + known central portals (`scholarships.gov.in`, `myscheme.gov.in`) | 1 | ✅ |
| `GOV_STATE` | State subdomains (`*.maharashtra.gov.in`, `*.karnataka.gov.in`, `*.up.gov.in`, `*.cgg.gov.in`, …) from a 36-state/UT token list | 1 | ✅ |
| `REGULATOR` | UGC, AICTE, NCERT, CSIR, DST bodies (registry) | 1 | ✅ |
| `UNIVERSITY` | `*.ac.in`, `*.edu.in`, `*.edu`, `*.ac.uk`, `*.res.in` | 2 | ✅ |
| `CORPORATE_CSR` / `FOUNDATION_TRUST` / `INTERNATIONAL` | Registry-**approved** organisation domains (incl. PSU CSR such as LIC, ONGC; foreign govt TLDs `*.gov.uk`, `*.go.jp`) | 2–3 | ✅ once approved |
| `APPLICATION_PLATFORM` | NSP, state portals, Vidyasaarathi, Buddy4Study **when linked from an official provider page** | 3 | Valid as `application_url` only |
| `AGGREGATOR` | buddy4study.com, scholarshipsinindia.com, vidyasaarathi.co.in (listings), shiksha.com, collegedunia.com, collegedekho.com, … | 4 | ❌ discovery only |
| `NEWS_BLOG` | News/education-news domains + `/blog/`, `/news/`, `/article/` paths | 5 | ❌ |
| `UNKNOWN` | everything else | 5 | ❌ until approved |

**Domain-drift check (a real case found while preparing this plan):** `wbmdfcscholarship.org`, the old West Bengal Aikyashree scholarship portal, now redirects to an unrelated Dubai real-estate site. Old aggregator links still point to it. Rule: if the final registered domain ≠ expected domain and is not an approved alias, or the page no longer contains the provider name, mark the source `BROKEN`. Its records then go to `NO_LONGER_VERIFIABLE`.

**Approved aliases** handle legitimate moves we observed. `aicte-india.org` → `aicte.gov.in`, and `glowandlovelycareers.in` → `glowupacademy.in` (a rebrand).

---

## 9. Filtering — a multi-stage funnel

| Stage | Where | Filters |
|---|---|---|
| **F1 URL filter** (before fetch) | Spider/middleware | Domain scope (registered domain + aliases) · robots.txt · allowed types (HTML, PDF) · deny patterns (§7.3) · canonicalisation (strip session IDs, tracking params, sort/filter params) · depth/budget · `next_crawl_at` not yet due → skip |
| **F2 Response filter** | Pipeline | HTTP status · **soft-404 detector** · content-type sniffing · min main-text length · language detection (EN/HI) |
| **F3 Page relevance** | `page_classifier.py` | Rule score → `DETAIL` / `LISTING` / `IRRELEVANT` (formula below) |
| **F4 Scholarship validity** | After extraction | Must have **name + provider + (benefit or eligibility)** with evidence. `funding_type` must be scholarship/fellowship/grant/award/fee-waiver. Pure loans are excluded. Notices that are results, merit lists or exam schedules are excluded. |
| **F5 India-eligibility** | After extraction | Domestic sources pass. International ones need evidence such as "Indian nationals/citizens", "India" in the eligible-country list, or "all nationalities". **Exclude** schemes *for foreign nationals studying in India* (e.g., "foreign nationals", "international students only"). |
| **F6 Dedup / merge** | Before persist | Fingerprint = normalised name **with year tokens removed** + provider registered domain. Fuzzy merge (rapidfuzz `token_set_ratio ≥ 92`, same provider). The same scheme on NSP + ministry page → **one record, two sources** (corroboration). A new cycle ("2026-27" vs "2025-26") → a *change*, not a new record. |

**Page relevance score (F3):**
```
relevance = 4·[strong term in <title>/<h1>]
          + 2·min(#canonical sections found, 4)       # eligibility, benefits, how-to-apply, documents, dates, selection
          + 2·[₹/Rs./INR amount present]
          + 1·[date present] + 1·[apply link present]
          − 4·[deny term in title/URL] − 3·[soft-404 signals]

DETAIL      if relevance ≥ 8 and ≥ 2 sections
LISTING     if ≥ 3 scholarship-like outlinks and link density > 0.4   → follow links, don't extract
IRRELEVANT  otherwise                                                 → revisit in 30 days
```
Thresholds get tuned on a labelled set of ~150 pages (Day 3). Optionally add a TF-IDF + Logistic Regression classifier as a second opinion once labels exist.

**Soft-404 detector.** This is needed: `ugc.gov.in/<bad-path>` returns HTTP 200 with an error page at `/ErrorPage/index?code=404`. A page is a soft 404 if any of these hold:
1. The title or h1 matches `not found|404|error|does not exist|page cannot be displayed`.
2. The final URL is the homepage or an error path while a deep link was requested.
3. Its simhash similarity is > 0.9 to the domain's learned 404 fingerprint. We fetch one random `/<uuid>` path per domain per week to learn it.

---

## 10. Extraction methodology (no LLM)

### 10.1 Steps
1. **Snapshot.** Store the raw bytes as `data/snapshots/<sha256>`. This is the immutable evidence base.
2. **Text.** HTML goes through trafilatura (main content, tables kept). The DOM is kept via lxml for section boundaries. PDFs go through pdfplumber per page. Unicode is NFKC-normalised. "Rs.", "Rs", "INR" and "₹" are normalised. **We keep an offset map back to the original text**, so evidence offsets stay valid.
3. **Section segmentation.** Walk headings (`h1–h6`, standalone `strong`/`b` lines, `th`, `dt`, accordion titles, PDF lines in title case or bold). Map each to a canonical section with the synonym dictionary (rapidfuzz `partial_ratio ≥ 85`):
   - `eligibility` ← Eligibility, Eligibility Criteria, Who can apply, Conditions of eligibility, पात्रता
   - `benefits` ← Benefits, Scholarship amount, Quantum of scholarship, Financial assistance, Value of award, Rate of scholarship
   - `dates` ← Important dates, Last date, Closing date, Deadline, Schedule, अंतिम तिथि
   - `documents` ← Documents required, Checklist, Enclosures, आवश्यक दस्तावेज़
   - `apply` ← How to apply, Application procedure, Mode of application, आवेदन
   - `selection` ← Selection process/criteria, Mode of selection
   - `renewal` ← Renewal, Continuation of scholarship
4. **Two extraction passes.** Pass A runs each field extractor **on its own section**. Pass B runs it on the **full text**. Agreement between them feeds the consistency signal (S8). Pass A wins when both find a value.
5. **Validate** with pydantic and sanity ranges: income ₹10k–₹1 crore; age 5–60; amount ₹500–₹1 crore; dates within the cycle year ± 1. An invalid value is **dropped**, not "fixed".
6. **Persist** as an `ExtractedField(value, evidence_text, locator, section, extractor, pass, snapshot_sha, source_url)`.

### 10.2 Field extractors

| Field | Method | Null rule |
|---|---|---|
| **Name** | `<h1>` → `og:title` → `<title>` (site suffix stripped). Must contain a strong term or match the registry scheme name. PDFs: first heading line containing scholarship/scheme/fellowship. | Required (otherwise not a record) |
| **Provider** | Registry org name for the domain. Otherwise the pattern `(offered|sponsored|funded|instituted) by …`, `Ministry of …`, `Department of …` in header or footer. | Required |
| **Official source URL** | Final canonical URL of the DETAIL page on an official-classified domain | Required |
| **Application URL** | Anchors matching `apply( now| online)?|online application|register|application form|आवेदन`. Resolved and classified: official/platform → `ONLINE`. Downloadable form PDF → `OFFLINE`. | NULL → "Not specified" |
| **Amount / benefit** | Regex `(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|crore|k)?(?:/-)?\s*(per (?:annum|year|month)|p\.?a\.?|monthly|annually|one[- ]time)?`. Handles the Indian numbering system (`1,00,000`) and ranges. Non-monetary benefits (fee waiver, laptop, mentorship) come from benefits-section bullets. | NULL |
| **Eligibility (text)** | Eligibility-section text and bullets, verbatim | NULL |
| **Academic** | `(\d{2,3}(?:\.\d+)?)\s?%` near `minimum|at least|secured|scored|aggregate`. `(\d(?:\.\d+)?)\s*(CGPA|SGPA)`. | NULL |
| **Education level / course** | Dictionary → enum `CLASS_1_8, CLASS_9_10, CLASS_11_12, DIPLOMA_ITI, UG, PG, PHD, POSTDOC`. Covers India-specific terms: *pre-matric* = up to Class 10, *post-matric* = Class 11 onwards. Course dictionary includes B.Tech/BE/MBBS/B.Sc → UG and M.Tech/MBA/M.Sc → PG. | NULL |
| **Income** | Amount regex inside a ±150-char window around `income`, combined with `not exceed|less than|up to|below|≤|maximum`. Takes the **cap** value. | NULL → "Not specified" (never guessed) |
| **Age** | `aged? … (\d{1,2})\s*(to|-|and)\s*(\d{1,2})\s*years`, `not (more|exceed\w*) than (\d{1,2}) years`, `below (\d{1,2}) years` | NULL |
| **Gender** | `FEMALE` only if a requirement phrase is present ("girl students", "only for women", "female candidates", "single girl child"). `ALL` only if stated explicitly ("boys and girls", "all students"). Otherwise NULL. | NULL |
| **Category** | Word-boundary regex for SC, ST, OBC, EWS, DNT, minority communities, PwD/Divyang (with % threshold), wards of ex-servicemen/defence, sports, orphan | NULL |
| **Domicile / state** | Gazetteer of 36 states/UTs plus old names (Orissa, Pondicherry) within ±100 chars of `domicile|resident|native|permanent resident`. **Never inferred from the portal's state.** | NULL |
| **Institution** | Patterns: `recognised by (UGC|AICTE|…)`, `government (college|school)`, `NIRF`, `studying in <state>`, named institutions | NULL |
| **Opening / closing date** | dateparser (`DATE_ORDER=DMY`, strict, day+month+year required) on date regexes. Label context: closing ← `last date|closing|deadline|apply (before|by)|ends on|till`; opening ← `opening|starts|commence|from`. Rows of "Important dates" tables are parsed as (event, date) pairs. `extended` near a date → `deadline_extended=true`. | NULL. `is_rolling=true` only if stated explicitly. |
| **Academic year / cycle** | `20\d\d[-–/]\d{2,4}` near scholarship/session/academic year | NULL |
| **Documents** | Bullets under the documents section, normalised by a dictionary (Aadhaar, income certificate, caste certificate, marksheets, bank passbook, bonafide, domicile, photo) | NULL |
| **Selection / renewal** | Section text, plus keyword tags (merit, interview, test, renewable, `% to continue`) | NULL |
| **Status words** | "applications closed", "portal is open", "coming soon", "last date extended" → inputs to the lifecycle engine | — |

### 10.3 Anti-hallucination guarantees
1. No generative model anywhere. Every value is a substring of, or a deterministic normalisation of, source text.
2. `repository.set_field()` refuses a value without evidence.
3. `verify_trace()` re-opens the snapshot and checks the snippet is present verbatim. If not, the field is dropped and logged.
4. Aggregator text is never a value source.
5. Range validators drop implausible values instead of "repairing" them.
6. The UI shows "Not specified" for NULLs and marks each displayed value with an evidence 🔗.

---

## 11. Verification engine and confidence score

### 11.1 Signals (weights in `config/scoring.yaml`, total = 100)

| ID | Signal (assignment question) | Weight | Scoring rubric (value 0–1) |
|---|---|---|---|
| **S1** | *Is the source official?* | **25** | Govt central/state/regulator **1.0** · university/research institute **0.95** · approved CSR/foundation/international provider domain **0.90** · provider-domain match, not yet approved **0.60** · application platform **0.30** · aggregator/blog **0.0** |
| **S2** | *Is it currently present on the official source?* | **15** | This run: HTTP 200, not a soft 404, **and** name found on the page (rapidfuzz partial_ratio ≥ 90) → **1.0** · name match 75–89 → **0.5** · otherwise **0**. Decay: last successful check > 14 days ago → ×0.5. |
| **S3** | *Is there an official application URL?* | **10** | Apply link on an official domain or govt portal **1.0** · third-party application platform linked *from the official page* **0.8** · official offline form/procedure **0.7** · none **0** |
| **S4** | *Can eligibility be directly supported?* | **10** | ≥ 3 structured criteria with evidence **1.0** · 2 → **0.8** · 1 → **0.5** · eligibility text only → **0.3** · none **0** |
| **S5** | *Can the deadline be directly supported?* | **10** | Explicit closing date with evidence **1.0** · explicitly rolling/"open throughout the year" **0.8** · month/cycle only **0.4** · none **0** |
| **S6** | *Is the information current?* | **10** | Current cycle evidenced (closing date ≥ today − 60 days, or current/next academic year mentioned, or `Last-Modified` ≤ 12 months) **1.0** · previous cycle **0.4** · older or undated **0.1** |
| **S7** | *Can important fields be traced to evidence?* | **10** | Share of non-null fields whose snippet is found verbatim in the stored snapshot |
| **S8** | *Was the information extracted consistently?* | **5** | Share of key fields (amount, closing date, income, education level, gender, category) where Pass A = Pass B, and the value is stable vs the previous crawl unless a change was detected with evidence |
| **S9** | *Are there corroborating sources?* | **5** | ≥ 2 **independent** official sources (different registered domains, e.g., ministry + NSP) agree on key fields **1.0** · single official source, no conflict **0.6** · only non-official corroboration **0.3** |

**Penalty P** (*are there conflicting sources?*): **−10 per unresolved conflict between official sources** on a key field (closing date, amount, income cap, education level), capped at −30. A newer-dated official notice wins and resolves the conflict. Aggregator disagreements are logged but carry no penalty, since aggregators are often stale.

```
raw_score  = Σ (weight_i × value_i) − P            # 0–100, one decimal
```

### 11.2 Hard gates (all required for VERIFIED)

| Gate | Condition |
|---|---|
| G1 Official source | S1 ≥ 0.90 |
| G2 Live now | S2 = 1.0 in the latest run |
| G3 Fully traceable | S7 = 1.0 |
| G4 No open conflicts | P = 0 |
| G5 Core identity | name, provider and official_source_url are all non-null |

```
confidence = raw_score                    if all gates pass
           = min(raw_score, 94.9)         otherwise (failed gates are listed in "Why this score?")

verification_status = VERIFIED if confidence ≥ 95 else REVIEW_REQUIRED
```

The gates make it **impossible** for a record that is aggregator-only, currently offline, or partly untraceable to show ≥ 95, however complete it looks.

### 11.3 Worked examples (illustrative inputs)

**A — Central-government scheme page on a `.gov.in` domain, applications via NSP**

| Signal | Value | Points |
|---|---|---|
| S1 govt | 1.0 | 25.0 |
| S2 live + name found | 1.0 | 15.0 |
| S3 apply via NSP | 1.0 | 10.0 |
| S4 4 criteria | 1.0 | 10.0 |
| S5 explicit closing date | 1.0 | 10.0 |
| S6 current cycle | 1.0 | 10.0 |
| S7 traceable | 1.0 | 10.0 |
| S8 5 of 6 agree | 0.83 | 4.2 |
| S9 single official source | 0.6 | 3.0 |
| **Total** | gates ✔ | **97.2 → VERIFIED** |

**B — Foundation scholarship on an approved `.org` domain, applications on a third-party platform, no deadline published yet**
S1 0.9 (22.5) + S2 15 + S3 0.8 (8) + S4 0.8 (8) + S5 0 + S6 0.4 (4) + S7 10 + S8 5 + S9 3 = **75.5 → REVIEW_REQUIRED**. Why: no deadline evidence; information is from the previous cycle.

**C — Record that scored 96.0 last week; this week the official page returns 404**
G2 fails → **94.9 → REVIEW_REQUIRED**. After 2 consecutive misses the lifecycle moves it to `NO_LONGER_VERIFIABLE`.

### 11.4 "Why this score?" (stored in `verification_checks`, rendered in the UI)
```
Confidence 97.2%  ·  VERIFIED  ·  scoring v1.0
✔ S1 Official source (+25.0/25)     aicte.gov.in classified REGULATOR (tier 1)
✔ S2 Live on source (+15.0/15)      HTTP 200 on 2026-11-13, name match 100
✔ S3 Application route (+10.0/10)   "Apply Online" → scholarships.gov.in (GOV_CENTRAL)
…
◐ S9 Corroboration (+3.0/5)         single official source, no conflicts
Gates: G1 ✔ G2 ✔ G3 ✔ G4 ✔ G5 ✔     Penalties: none
```

### 11.5 Calibration (so the weights are not arbitrary)
- Build a **golden set of 30–40 records**, verified by hand field by field against the official page.
- Measure **VERIFIED-bucket precision**: target **100%** correct on key fields. Also measure field-level accuracy, target ≥ 95%.
- Adjust weights and thresholds in `scoring.yaml`. Bump `scoring_version` and store it on every score, so changes stay auditable.

---

## 12. Lifecycle status rules (evaluated after every run, first match wins)

| Status | Rule |
|---|---|
| `NO_LONGER_VERIFIABLE` | Official page is 404/410/soft 404, **or** the name is missing from it on **≥ 2 consecutive successful crawls**, **or** the source domain drifted (`BROKEN`), **or** the domain has been unreachable for ≥ 3 attempts spanning ≥ 72 h |
| `EXPIRED` | `closing_date < today` **and** the post-deadline extension checks (D+1, D+3, D+7) found no new date. **Or** the official page says "applications closed" for the current cycle. |
| `REVIEW_REQUIRED` | Confidence < 95, **or** an open official conflict, **or** contradicted by a newer official notification (a newer-dated notice on the same source mentions the scheme plus *discontinued / withdrawn / revised / corrigendum*) |
| `EXPIRING_SOON` | `today ≤ closing_date ≤ today + 15 days` |
| `ACTIVE` | VERIFIED and (`closing_date > today + 15` or explicitly rolling/open) |

`verification_status` (VERIFIED/REVIEW_REQUIRED) is stored **separately** from lifecycle `status`. An expired scholarship can still be a *verified* record of an expired opportunity.

---

## 13. Change detection and versioning

| Level | Mechanism | Outcome |
|---|---|---|
| L0 HTTP | `If-None-Match` / `If-Modified-Since` from stored `etag`/`last_modified` | `304` → unchanged. Bump `last_verified_at` and stop. |
| L1 Content | SHA-256 of **normalised main text** (strips visitor counters, "last updated" stamps, CSRF tokens, dates in the footer) | Same hash → unchanged |
| L2 Field diff | Re-extract and compare normalised values (dates as ISO, amounts as integers, lists as sets) | One `field_changes` row per changed field: **old value, new value, date detected, source URL, old and new evidence IDs**. New `scholarship_versions` row. Current row updated. |
| L3 Record events | Per run: `NEW`, `UNCHANGED`, `CHANGED`, `MISSING`, `REAPPEARED`, `EXPIRED`, `STATUS_CHANGED` | `run_events` + `crawl_runs` summary ("Run 2: A unchanged · B deadline changed · C no longer available · D new") |

**Removal handling:** a scholarship missing from a *listing* is not "removed". Its detail page is re-checked directly, and 2 consecutive misses are required. This prevents false removals during government-site outages.

**Getting real change and expiry examples within the assignment window, honestly:**
1. **Live re-crawls from Day 2.** Mid-November is peak NSP/state-portal season, and deadline extensions are common, so daily runs are likely to catch real deadline changes.
2. **Wayback historical baseline.** For key official pages, fetch an older snapshot through the Internet Archive CDX API (`web.archive.org/cdx/search/cdx?url=…&output=json`, then `web.archive.org/web/<ts>id_/<url>`). Process it as "Run 0 (historical)" with `snapshot.origin = WAYBACK`, then crawl live. Differences such as last cycle's deadline vs this cycle's are **real changes on the official source**. They are labelled "historical baseline" in the UI.
3. **Synthetic fixtures for tests only.** A local `http.server` serves v1/v2 fixture pages to test the diff logic deterministically. These live in `tests/` and a separate test DB, and **never** appear in the submitted dataset.
4. **Expired/stale examples.** Previous-cycle pages with past deadlines, pages that now 404, and lapsed domains (e.g., `wbmdfcscholarship.org`).

---

## 14. Frequency (recrawl) policy

### 14.1 Base intervals by source type (`config/frequency.yaml`)

| What | In season (Jul–Jan) | Off season | Notes |
|---|---|---|---|
| National hubs (NSP, myScheme) | 24 h | 72 h | Hub/listing pages only |
| State scholarship portals | 24 h | 72 h | |
| Ministries / regulators (MoE, MSJE, MoTA, MoMA, UGC, AICTE) | 72 h | 7 d | "Notices/what's new" pages every 24 h |
| Universities | 7 d | 14 d | |
| CSR / foundations / trusts | 7 d | 14 d | 24 h while their application window is open |
| International | 7 d | 14 d | |
| Aggregator lead mining | 24 h | 72 h | Listings and sitemaps only, filtered by `lastmod` |
| Pages classified IRRELEVANT | 30 d | 30 d | |
| New discovered domain (PENDING) | Once, then on approval | — | |

### 14.2 Adaptive interval for known scholarship detail pages
```
interval = clamp(base × 1.5^unchanged_streak, min=12h, max=14d)

overrides (first match):
  closing_date within 3 days            → 12 h
  closing_date within 15 days           → 24 h
  closing_date just passed              → D+1, D+3, D+7  (extension watch), then 30 d until next cycle
  opening_date within 7 days            → 24 h
  page changed in this run              → reset streak; interval = min(base, 24 h)
fetch failures                          → retry 1 h → 6 h → 24 h; ≥3 failures over ≥72 h → NO_LONGER_VERIFIABLE candidate
```
Stored as `pages.next_crawl_at`. The `recheck` spider loads only due URLs.

### 14.3 Run cadence
- **Daily `recheck` run** at **02:00 IST** (`30 20 * * *` UTC cron), the off-peak window for government servers.
- **Discovery run** (seeds + sitemaps + aggregator leads): daily in season, twice weekly otherwise.
- **Per-run safety budget:** ≤ 3,000 requests, ≤ 300 pages per domain.

---

## 15. Anti-blocking strategy (polite crawling)

Anti-blocking here means **not giving sites a reason to block us**, not evading blocks.

| Measure | Implementation |
|---|---|
| Obey robots.txt | `ROBOTSTXT_OBEY = True` (Protego). Note: some sites (UGC, Reliance Foundation) return an HTML page for `/robots.txt`. That is treated as no rules, so we fall back to conservative limits. |
| Honest identity | `USER_AGENT = "AtlasScholarshipBot/0.1 (+https://github.com/<you>/scholarship-crawler)"`. **No UA rotation or browser impersonation.** |
| Low concurrency | `CONCURRENT_REQUESTS_PER_DOMAIN = 2` (1 for fragile government sites via per-source override) |
| Delays + AutoThrottle | `DOWNLOAD_DELAY = 2`, `RANDOMIZE_DOWNLOAD_DELAY = True`, AutoThrottle target concurrency 1.0, max delay 60 s |
| Honour back-pressure | Retry on 429/5xx with exponential backoff. Respect the `Retry-After` header (custom middleware). |
| Per-domain circuit breaker | ≥ 5 consecutive 403/429/5xx/timeouts → drop the rest of that domain's queue this run, set `paused_until = now + 24 h` |
| Fetch less | Conditional GETs (ETag/Last-Modified), adaptive frequency, sitemap `lastmod`, URL canonicalisation + dupefilter, crawl budgets, `HTTPCACHE_ENABLED` during development |
| Off-peak scheduling | 01:00–06:00 IST |
| Lightweight requests | Skip images, CSS, fonts and video. In Playwright, block resource types other than document/script/xhr. |
| Hard limits | No login-gated pages, no CAPTCHA solving, no form submission beyond pagination postbacks. **No proxies** (paid ones are disallowed; free proxy lists are unsafe and abusive). |
| When blocked anyway | Mark the source `BLOCKED`. Use alternative official pages for the same scheme (e.g., the NSP listing or a ministry PDF). Never present unverified data as verified. *Observed while preparing this plan: azimpremjifoundation.org and shiksha.com return 403 to bots.* |

**`settings.py` core:**
```python
BOT_NAME = "atlas_crawler"
USER_AGENT = "AtlasScholarshipBot/0.1 (+https://github.com/<you>/scholarship-crawler)"
ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 2.0
RANDOMIZE_DOWNLOAD_DELAY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 60
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
DOWNLOAD_TIMEOUT = 45
DOWNLOAD_MAXSIZE = 25 * 1024 * 1024          # PDFs
RETRY_TIMES = 3
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504, 522, 524]
DEPTH_LIMIT = 4                               # per-source max_depth enforced in spider
DEFAULT_REQUEST_HEADERS = {"Accept-Language": "en-IN,en;q=0.9,hi;q=0.6"}
HTTPCACHE_ENABLED = False                     # True in dev profile
DOWNLOADER_MIDDLEWARES = {
    "atlas_crawler.middlewares.ConditionalGetMiddleware": 540,
    "atlas_crawler.middlewares.DomainCircuitBreakerMiddleware": 545,
    "atlas_crawler.middlewares.RetryAfterMiddleware": 550,
}
ITEM_PIPELINES = {
    "atlas_crawler.pipelines.SnapshotPipeline": 100,
    "atlas_crawler.pipelines.PagePersistPipeline": 200,
    "atlas_crawler.pipelines.LeadPipeline": 300,
}
```

---

## 16. Automation and deployment

| Piece | Choice |
|---|---|
| CLI | `atlas run` (crawl, process, verify, lifecycle, report), `atlas crawl --mode discover|recheck|full`, `atlas export`, `atlas audit` |
| Local scheduler | `atlas schedule` (APScheduler daemon) **or** Windows Task Scheduler calling `atlas run` |
| Live automation | `.github/workflows/crawl.yml`: cron `30 20 * * *` → set up Python and Playwright → `atlas run` → commit `data/atlas.db` and new evidence snapshots (text-only, gzipped) to a `data` branch |
| Live dashboard | **Streamlit Community Cloud** (free) reading `data/atlas.db` from the repo. It redeploys on each data commit. |
| DB upgrade path | Set `DB_URL` to Supabase Postgres (free tier). SQLAlchemy models are unchanged. FTS moves to `tsvector`. |

---

## 17. Streamlit dashboard spec

**Overview (`app.py`)**
- KPI tiles: **Total discovered · Verified · Review required · Active · Expiring soon · Expired · No longer verifiable · Recently updated (7 d) · Average confidence**
- Charts: records by source type; status distribution; confidence histogram with a 95% line; deadlines in the next 30 days.
- Last run card: time, duration, pages fetched, NEW/CHANGED/MISSING counts.

**Scholarships (searchable list)**
- Full-text search (SQLite FTS5) over name, provider and eligibility.
- Filters: status, verification, source type, education level, category, gender, state, deadline range, min confidence.
- `st.dataframe` with row selection opens the **detail view**:
  - Header: name, provider, status badge, VERIFIED/REVIEW badge, confidence.
  - All schema fields, with **"Not specified"** for NULLs and an evidence 🔗 next to each value.
  - Links: **Official source**, **Apply**.
  - **Why this score?** Per-signal earned/possible points, reasons, gates ✔/✗, penalties.
  - **Last verified** timestamp, plus the source pages list (primary and corroborating).
  - **Change history**: table of field, old → new, detected at, source, evidence (both snippets).
  - **Source evidence**: per-field verbatim snippet with the value highlighted, snapshot SHA, locator, and a link to the stored copy.

**Changes**: global "CHANGE DETECTED" feed across all scholarships.
**Crawl runs**: run history table and per-run events. A "Run now" button appears only when running locally.
**Sources & Discovery**: source registry (type, tier, approval, last crawl, failures), pending discovered domains, aggregator leads with resolution status.

Implementation notes: read-only SQLite connection, `st.cache_data(ttl=300)`, plotly charts, no heavy styling.

---

## 18. Seed sources (checked 13 Nov 2026)

"200" means the URL responded HTTP 200 to a single polite request from our checker on 13 Nov 2026. Notes record quirks the crawler must handle. Scheme-specific deep pages will be found by the focused crawl rather than hard-coded.

### 18.1 Central government, regulators and PSUs
| Source | Seed URL | Type | Check | Notes |
|---|---|---|---|---|
| National Scholarship Portal | https://scholarships.gov.in/ | GOV_CENTRAL | 200 | Main hub. No robots.txt (404, so allow). Some parts JS-heavy. |
| myScheme (Govt of India scheme catalogue) | https://www.myscheme.gov.in/search/category/Education%20%26%20Learning | GOV_CENTRAL | 200 | **JS-rendered.** Sitemap lists only 38 URLs. Playwright. Discovery + corroboration. |
| Ministry of Education – Scholarships | https://www.education.gov.in/scholarships | GOV_CENTRAL | 200 | |
| AICTE – Student Development Schemes | https://www.aicte.gov.in/schemes/students-development-schemes | REGULATOR | 200 | Old `aicte-india.org` fails TLS → alias |
| UGC | https://www.ugc.gov.in/ | REGULATOR | 200 | **Soft-404s** on bad paths. robots.txt returns HTML. |
| Ministry of Social Justice & Empowerment | https://socialjustice.gov.in/ | GOV_CENTRAL | 200 | SC/OBC schemes |
| Ministry of Tribal Affairs | https://tribal.nic.in/ | GOV_CENTRAL | 200 | Redirects to a **Hindi** home page; use the English path |
| National Overseas Scholarship (ST) | https://overseas.tribal.gov.in/ | GOV_CENTRAL | 200 | International study for ST students |
| Ministry of Minority Affairs | https://www.minorityaffairs.gov.in/ | GOV_CENTRAL | 200 | |
| Dept. of Empowerment of Persons with Disabilities | https://depwd.gov.in/ | GOV_CENTRAL | 200 | PwD scholarships |
| Kendriya Sainik Board (PM Scholarship Scheme) | https://ksb.gov.in/ | GOV_CENTRAL | 200 | Defence wards. Incomplete TLS chain. |
| Dept. of Ex-Servicemen Welfare | https://desw.gov.in/ | GOV_CENTRAL | 200 | |
| INSPIRE (DST) | https://online-inspire.gov.in/ | GOV_CENTRAL | 200 | Incomplete TLS chain |
| Prime Minister's Research Fellowship | https://pmrf.in/ | GOV_CENTRAL | 200 | Research |
| CSIR HRDG (fellowships) | https://csirhrdg.res.in/ | REGULATOR | 200 | Research |
| LIC Golden Jubilee Foundation | https://licindia.in/golden-jubilee-foundation | CORPORATE_CSR (PSU) | 200 | |
| ONGC Scholarship | https://www.ongcscholar.org/ | CORPORATE_CSR (PSU) | 200 | TLS hostname mismatch. Confirm ownership via a link from ONGC's main site before approving. |

### 18.2 State scholarship portals
| State | Seed URL | Check | Notes |
|---|---|---|---|
| Maharashtra (MahaDBT) | https://mahadbt.maharashtra.gov.in/ | 200 | |
| Karnataka (SSP post-matric) | https://ssp.postmatric.karnataka.gov.in/ | 200 | |
| Uttar Pradesh | https://scholarship.up.gov.in/ | 200 | |
| Bihar (Post-Matric) | https://pmsonline.bihar.gov.in/ | 200 | ASP.NET (`Default.aspx`) |
| Telangana (ePASS) | https://telanganaepass.cgg.gov.in/ | 200 | |
| Andhra Pradesh (Jnanabhumi) | https://jnanabhumi.ap.gov.in/ | 200 | |
| Kerala (DCE scholarships) | https://dcescholarship.kerala.gov.in/ | 200 | |
| Odisha (State Scholarship Portal) | https://scholarship.odisha.gov.in/ | 200 | |
| Punjab | https://scholarships.punjab.gov.in/ | 200 | |
| Haryana (Higher Education) | https://harchhatravratti.highereduhry.ac.in/ | 200 | |
| Gujarat (Digital Gujarat) | https://www.digitalgujarat.gov.in/ | 200 | |
| West Bengal (SVMCM) | https://svmcm.wbhed.gov.in/ | timeout | Keep with low priority; retry |

### 18.3 Universities and institutions
| Institution | Seed URL | Check |
|---|---|---|
| University of Delhi | https://www.du.ac.in/ | 200 |
| Jawaharlal Nehru University | https://www.jnu.ac.in/ | 200 |
| Banaras Hindu University | https://www.bhu.ac.in/ | 200 |
| Aligarh Muslim University | https://www.amu.ac.in/ | 200 |
| IIT Bombay | https://www.iitb.ac.in/ | 200 |
| IIT Madras | https://www.iitm.ac.in/ | 200 |
| IIT Delhi | https://home.iitd.ac.in/ | 200 (www redirects here) |
| Ashoka University | https://www.ashoka.edu.in/ | 200 |

### 18.4 Corporate CSR
| Programme | Seed URL | Check | Notes |
|---|---|---|---|
| Reliance Foundation Scholarships | https://www.scholarships.reliancefoundation.org/ | 200 | **SPA** → Playwright. robots.txt returns HTML. |
| Aditya Birla Scholars | https://www.adityabirlascholars.net/ | 200 | |
| Santoor Women's Scholarship (Wipro) | https://www.santoorscholarships.com/ | 200 | Girls only, a gender-criteria test case |
| Kotak Kanya Scholarship | https://www.kotakeducation.org/kotak-kanya-scholarship/ | 200 | Applications may go via a platform → APPLICATION_PLATFORM case |
| L'Oréal India For Young Women in Science | https://www.foryoungwomeninscience.com/ | 200 | |
| Glow & Lovely Careers (rebranded) | https://www.glowupacademy.in/ | 200 | Old `glowandlovelycareers.in` redirects here → alias |

### 18.5 Foundations and trusts
| Foundation | Seed URL | Check | Notes |
|---|---|---|---|
| J N Tata Endowment | https://www.jntataendowment.org/ | 200 | Higher studies abroad (loan-scholarship; check `funding_type`) |
| Tata Trusts – Education Grants | https://www.tatatrusts.org/explore-our-work/enabling-human-potential/individual-grants/education-grants | 200 | Old path redirects here |
| K C Mahindra Education Trust | https://www.kcmet.org/ | 200 | |
| Inlaks Shivdasani Foundation | https://inlaksfoundation.org/ | 200 | |
| Narotam Sekhsaria Foundation | https://pg.nsfoundation.co.in/ | 200 | |
| Sitaram Jindal Foundation | https://www.sitaramjindalfoundation.org/scholarships-for-students-in-bangalore.php | 200 | |
| Vidyadhan (Sarojini Damodaran Foundation) | https://www.vidyadhan.org/ | 200 | |
| Foundation for Excellence | https://ffe.org/ | 200 | |
| Dakshana Foundation | https://www.dakshana.org/ | 200 | |

### 18.6 International (open to Indian nationals)
| Programme | Seed URL | Check |
|---|---|---|
| Commonwealth Scholarships (UK) | https://cscuk.fcdo.gov.uk/scholarships/ | 200 |
| Fulbright-Nehru (USIEF) | https://www.usief.org.in/ | 200 |
| DAAD India | https://www.daad.in/en/find-funding/ | 200 |
| Rhodes Scholarship – India | https://www.rhodeshouse.ox.ac.uk/scholarships/applications/india/ | 200 |
| Gates Cambridge | https://www.gatescambridge.org/ | 200 |
| Study in Japan (MEXT info) | https://www.studyinjapan.go.jp/en/planning/scholarships/about-scholarships/ | 200 |
| Chevening – India | https://www.chevening.org/scholarship/india/ | timeout — retry |

### 18.7 Aggregators (discovery only, never authoritative)
| Aggregator | URL | Check | Notes |
|---|---|---|---|
| Buddy4Study | https://www.buddy4study.com/scholarships | 200 | robots.txt allows. Sitemap index with 16 child sitemaps. Also hosts some CSR applications (APPLICATION_PLATFORM role only). |
| Vidyasaarathi | https://www.vidyasaarathi.co.in/ | 200 | Also an application platform for CSR schemes |
| ScholarshipsInIndia | https://www.scholarshipsinindia.com/ | 200 | |

### 18.8 Excluded or problem URLs (kept as **test cases**)
| URL | Observation (13 Nov 2026) | Use |
|---|---|---|
| wbmdfcscholarship.org | Redirects to an unrelated Dubai real-estate site (lapsed domain) | Domain-drift / `NO_LONGER_VERIFIABLE` test |
| ugc.gov.in/ugc_schemes | HTTP 200 but lands on `/ErrorPage/index?code=404` | Soft-404 test |
| aicte-india.org | TLS handshake failure; superseded by aicte.gov.in | Alias handling |
| azimpremjifoundation.org, shiksha.com/scholarships | HTTP 403 to bots | Respect the block; `BLOCKED` source |
| dbtindia.gov.in, scholarshipportal.mp.nic.in, yet.nta.ac.in | Did not resolve (DNS) from our check | Re-test before adding |
| nosmsje.gov.in | Timeout | Re-test; low priority |
| collegedunia.com/scholarships | 404 | Drop |

### 18.9 `config/sources.yaml` entry format
```yaml
- key: aicte_student_schemes
  name: AICTE – Student Development Schemes
  provider: All India Council for Technical Education
  source_type: REGULATOR
  trust_tier: 1
  seeds:
    - https://www.aicte.gov.in/schemes/students-development-schemes
  registered_domain: aicte.gov.in
  domain_aliases: [aicte-india.org]
  render_js: false
  max_depth: 3
  page_budget: 200
  base_frequency_hours: 72
  approval_status: APPROVED

- key: buddy4study
  name: Buddy4Study
  source_type: AGGREGATOR          # leads only
  trust_tier: 4
  seeds: [https://www.buddy4study.com/scholarships]
  sitemaps: [https://www.buddy4study.com/sitemap.xml]
  registered_domain: buddy4study.com
  role: DISCOVERY_ONLY
  base_frequency_hours: 24
```

---

## 19. Delivery plan (12 working days)

The plan can be compressed to about 8 days by merging Days 8–9 and 10–11. **Start the daily scheduled crawl on Day 2**, so real change history builds up while the rest is being built.

| Day | Phase | Tasks | Exit criteria |
|---|---|---|---|
| 1 | **Foundations** | Repo, venv, `requirements.txt`, Scrapy skeleton, SQLAlchemy models + DDL, YAML configs, `sources.yaml` from §18, logging, Typer CLI stub | `atlas crawl --source aicte_student_schemes` stores pages + snapshots |
| 2 | **Crawler** | `official` spider with link scoring, domain scoping, PDF following, sitemap spider, polite settings, ConditionalGet + CircuitBreaker + Retry-After middlewares, Playwright for 2 JS sources, **turn on the daily schedule** | ≥ 500 pages from ≥ 25 sources. Zero robots violations. |
| 3 | **Classification & filtering** | Source classifier + drift check + aliases, soft-404 detector, page relevance scorer, listing/detail, dedup fingerprint. Label ~150 pages. | DETAIL precision ≥ 90% on the labelled set |
| 4–5 | **Extraction** | Content + offset map, section segmentation, all field extractors (§10.2), PDF path, validators, evidence capture, `verify_trace()`, unit tests on saved real fixtures | ≥ 25 records with evidence. Invariant test passes. |
| 6 | **Verification engine** | Signals S1–S9, gates, penalties, corroboration/merge across sources, `verification_checks`, `scoring.yaml` | Every record has a score + breakdown. ≥ 10 at ≥ 95. |
| 7 | **Change & lifecycle** | L0–L3 change detection, versions, run events, status rules, Wayback baseline for ~10 key pages | ≥ 2 real field changes. ≥ 2 expired/stale. |
| 8 | **Discovery expansion + scheduling** | Aggregator lead miner, resolver (outlink → name match → Wikidata), outlink domain expansion, auto-approval rules, adaptive frequency, GitHub Actions workflow | ≥ 5 scholarships whose source was **not** in the seed list |
| 9 | **Dashboard** | All pages in §17 | Dashboard answers "show us what your crawler found" |
| 10 | **QA & calibration** | Golden-set audit (30–40 records, field by field), fix extractors, re-tune weights, remove any unsupported value | VERIFIED precision 100% on the audit. Field accuracy ≥ 95%. |
| 11 | **Ship** | Deploy to Streamlit Cloud, README (setup/run/demo), `TECHNICAL_NOTE.md` (≤ 3 pages), `data/sample/` export | Live URL works from a clean browser |
| 12 | **Demo & buffer** | Record the demo (§22), final checks against §21 | Submission ready |

**Critical path:** Crawler → Extraction → Verification → Lifecycle → Dashboard. The discovery expansion (Day 8) and the dashboard (Day 9) can be worked on in parallel if time is short.

---

## 20. Testing and QA

| Test | What it proves |
|---|---|
| Extractor unit tests on **saved real pages** (HTML + PDF) with expected values | Accuracy per field; regressions caught |
| `test_invariants.py`: every non-null field has a current evidence row whose snippet is in the snapshot | Traceability / anti-hallucination |
| `test_confidence.py`: signal inputs → exact score; each gate failure caps at 94.9 | Score is deterministic, not arbitrary |
| `test_lifecycle.py` using a local fixture server (v1 → v2 pages) | Change detection, MISSING → NO_LONGER_VERIFIABLE after 2 misses, EXPIRED after the extension watch |
| `test_soft404.py`: UGC-style error redirect, "Page not found" titles | Liveness can't be faked |
| `scripts/audit_sample.py`: random 20 VERIFIED records → CSV with snippets for manual review | Human spot-check, mirroring how the graders will inspect |

---

## 21. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Government sites slow, down or with broken TLS (observed) | Retries + backoff, 72 h grace, 2-miss rule, certificate errors don't block fetches |
| JS-only portals (myScheme, Reliance) | Playwright for flagged sources only. Use public JSON endpoints the page itself calls. |
| Details only in PDFs, some scanned | pdfplumber. Optional OCR. Otherwise REVIEW (never guess). |
| Soft-404s and lapsed domains (both observed) | Soft-404 detector, domain-drift check, aliases registry |
| CSR applications hosted on aggregators | `APPLICATION_PLATFORM` role: valid `application_url` only when linked from the official page |
| Seasonality (many schemes closed) | Store every cycle with correct EXPIRED status. Prioritise open ones (Nov is peak NSP season). |
| Too few real changes in the window | Daily runs from Day 2 + Wayback historical baselines (labelled) |
| Duplicates across NSP / ministry / state | Fingerprint + fuzzy merge. Duplicates become corroboration. |
| Heterogeneous layouts → wrong values | Section-scoped extraction, two-pass consistency, validators, gates, golden-set audit |
| Being blocked | Polite settings, circuit breaker, alternative official sources. No evasion. |
| scrapy-playwright on Windows | Use a recent release. Fall back to WSL or the Linux CI runner for JS sources. |

---

## 22. Submission checklist and demo

**A. Working application:** Streamlit Community Cloud URL + local install instructions.
**B. GitHub repo:** source code, README, `requirements.txt`, DB schema (`atlas/db/models.py` + `schema.sql`), `config/`, `data/sample/` (JSON + CSV with evidence), setup instructions.
**C. Demo video (3–5 min), following the brief:**
1. `atlas run`: crawler starts (show the Scrapy log + run row).
2. **Discovers**: show a lead found via an aggregator and resolved to an official domain not in the seeds.
3. **Extracts**: open the snapshot, show snippets → values.
4. **Identifies official source**: the source classification + "Why this score?" S1.
5. **Verifies + scores**: breakdown and gates.
6. **Stores**: query `data/atlas.db` (DB Browser for SQLite or the `sqlite3` CLI).
7. **Displays**: dashboard KPIs → search → detail page.
8. **Runs again**: second run summary (unchanged / changed / missing / new).
9. **Detects changes**: change history with old/new values, date, source and evidence. An expired/no-longer-verifiable example.

**D. `TECHNICAL_NOTE.md` (≤ 3 pages):** architecture diagram · technology choices (and *why no LLM*) · discovery methodology (§7) · extraction (§10) · verification and confidence formula with gates (§11) · anti-hallucination guarantees (§10.3) · change detection (§13) · known limitations. **Only describe what is built.**

**Final self-check before submitting:**
- [ ] ≥ 20 records, ≥ 15 official-verified, ≥ 10 at ≥ 95%, ≥ 3 source types
- [ ] ≥ 2 real field changes and ≥ 2 expired/stale, with evidence
- [ ] Every displayed value has an evidence link. NULLs show "Not specified".
- [ ] No record's official source is an aggregator or blog
- [ ] At least some scholarships come from sources discovered at runtime (not in `sources.yaml`)
- [ ] Scheduled run has executed automatically at least 3 times
- [ ] Only free/open-source tools used
