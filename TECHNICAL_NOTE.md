# Technical Note — Scholarship Intelligence Crawler

*Describes only what is built and running in this repository.*

## 1. Architecture

```
config/sources.yaml (≈60 official hubs) ─┐
                                          ▼
[recheck spider]  conditional re-fetch of every page that is the source of a record
[leads spider]    aggregator pages → (scholarship name, outbound links) only
[resolver]        outbound link on official TLD / provider-matching domain → new source (crawled same run)
[official spider] best-first focused crawl inside each official domain (+ outlink discovery of new
                  gov/edu domains) → immutable snapshots (sha256) of HTML and PDF
        │
        ▼
[process]   HTML/PDF → deterministic text blocks → section segmentation → page classifier
            (DETAIL / LISTING / IRRELEVANT / SOFT_404 / DRIFTED) → rule-based extraction
            (single-scheme pages and multi-scheme "card" listings) → evidence check → upsert + field diff
[enrich]    card records take eligibility/amount/… from their official guideline document (same domain)
[verify]    9 evidence signals + 5 gates → confidence, VERIFIED / REVIEW_REQUIRED, "why this score"
[lifecycle] ACTIVE · EXPIRING_SOON · EXPIRED · REVIEW_REQUIRED · NO_LONGER_VERIFIABLE
        ▼
SQLite (data/atlas.db) ──► Streamlit dashboard (read-only)
```

## 2. Technology choices

Python 3, **Scrapy** (scheduler, robots.txt, AutoThrottle, retries), **lxml** (DOM), **pdfplumber** (PDF text with
page numbers), **rapidfuzz** (name matching / dedup), **tldextract** (registered domains), **SQLAlchemy + SQLite**
(free, single inspectable file, Postgres-ready), **Streamlit + plotly** (UI), **Typer** (CLI), **APScheduler** /
GitHub Actions (scheduling). **No LLMs**: the brief's main risk is fabricated or unsupported data, so every value
is produced by a deterministic rule whose output is a substring/normalisation of the official text.

## 3. Discovery methodology

Hybrid "seeded focused crawl" — seeding alone would be a hard-coded list; an unbounded crawl of government sites
is mostly tenders/RTI/circulars.
1. **Seeds**: official hubs only (NSP, ministries, UGC/AICTE, state portals, universities, CSR, trusts,
   international programmes).
2. **Focused crawl**: link score = strong terms (scholarship, fellowship, chhatravritti, post-matric…) in anchor (+3)
   / URL (+2), weak terms (+1), relevant PDF (+1), deny terms (tender, recruitment, result…) (−5). Follow if ≥ 2
   (≥ 1 from the hub); Scrapy priority = 10·score − depth; per-source depth and page budgets.
3. **Outlink expansion**: a scholarship-like link from an official page to another gov/edu domain registers a new
   source (auto-approved by TLD rule) and crawls it in the same run.
4. **Aggregator lead mining**: Buddy4Study/ScholarshipsInIndia pages yield only the scholarship name and outbound
   links. The resolver accepts an outbound link if its domain is an official TLD (auto-approved) or matches the
   provider name (crawled but typed `PROVIDER_UNAPPROVED`, which can never reach VERIFIED). Unresolved leads are
   shown in the dashboard and never become records.

## 4. Extraction methodology

* **Text**: boilerplate (nav/menus/footers/cookie bars) removed — but never a wrapper holding > 40 % of the page;
  tabs/accordions/icon-lists are paired with their panes so labels become headings. The resulting text is stored
  next to the raw snapshot and is the evidence base.
* **Sections**: headings (h1–h6, bold lines, "Label:" lines, table label cells) mapped to eligibility / benefits /
  dates / documents / apply / selection / renewal via a synonym lexicon (EN + HI).
* **Fields**: dedicated extractors — Indian-format amounts (₹/Rs/INR, lakh/crore, per annum/month) excluding income
  and fee contexts; income caps ("income … not exceed …"); age; gender (requirement phrases only); categories (SC/ST/
  OBC/EWS/minority/PwD/defence…, flagged "(reservation)" when the sentence is a quota statement); education level
  (study-cue phrases: "enrolled in", "pursuing" …); % / CGPA; domicile (state gazetteer near "domicile/resident");
  dates (DMY numeric, textual, ISO) labelled closing/opening by context, skipping verification/result/interview dates;
  application link (classified official / platform / offline form).
* **Two passes**: section-scoped (A) and full-text (B). A wins; B is a fallback only when the page has no
  eligibility section; A/B agreement feeds the consistency signal.
* **Card listings**: a card = smallest DOM container around a scholarship-name element that contains a dated
  closing label and no other name (generic rule; e.g. the NSP "All Scholarships" page).

## 5. Verification & confidence-score methodology

`score = Σ wᵢ·sᵢ − penalty`, weights in `config/scoring.yaml`:

| Signal | w | Rubric |
|---|---|---|
| S1 Official source | 25 | govt/regulator 1.0 · university 0.95 · approved CSR/trust/international 0.9 · unapproved provider 0.6 · aggregator 0 |
| S2 Live on official source now | 15 | HTTP 200, not soft-404, name match ≥ 90 → 1.0; 75–89 → 0.5; ×0.5 if last success > 14 days |
| S3 Official application route | 10 | official 1.0 · platform linked from official page 0.8 · offline form 0.7 · none 0 |
| S4 Eligibility supported | 10 | ≥ 3 structured criteria 1.0 · 2 → 0.8 · 1 → 0.5 · text only 0.3 |
| S5 Deadline supported | 10 | explicit date 1.0 · explicitly rolling 0.8 · cycle only 0.4 |
| S6 Current | 10 | closing ≥ today−60 d / current academic year / page says open 1.0 · previous cycle 0.4 · else 0.1 |
| S7 Traceable | 10 | share of evidence rows re-verified verbatim in the stored snapshot |
| S8 Consistent | 5 | share of key fields where pass A = pass B |
| S9 Corroborated | 5 | named on another official domain 1.0 · single official source 0.6 |
| Penalty | −10 each | conflicting closing dates between official sources (same cycle), cap −30 |

**Gates** (all required for VERIFIED): G1 S1 ≥ 0.9 · G2 S2 = 1 · G3 S7 = 1 · G4 no conflicts · G5 name, provider and
official URL present. If any gate fails the score is capped at 94.9 → REVIEW_REQUIRED. Every signal value is stored
with a reason (`verification_checks`) and rendered as "Why this score?".

## 6. Anti-hallucination approach

No generative model; values are substrings/normalisations of source text. `prune_untraceable()` drops any field whose
evidence is not found verbatim at its recorded offsets **before** anything is written; scholarship columns are
re-derived from current evidence rows, so a value cannot exist without evidence. Missing → NULL → "Not specified".
No cross-field inference (e.g. domicile is never inferred from a state portal). Range validators drop implausible
values. Aggregator text is never a value source.

## 7. Change detection & stale data

Conditional GET (304) → content hash → field-level diff of the fields **owned by that page** → `field_changes` (old
value, new value, date, source, old/new evidence) + `scholarship_versions` + run events (NEW / UNCHANGED / CHANGED /
MISSING / REAPPEARED / STATUS_CHANGED). Old values are never overwritten. Stale rules: page 404/410/soft-404/name
missing on 2 consecutive checks or domain drift → NO_LONGER_VERIFIABLE; closing date passed (page re-checked after
the deadline, no extension) or page says closed → EXPIRED. Adaptive recrawl: scholarship pages daily with ×1.5
back-off when unchanged, 12 h within 3 days of a deadline, D+1/D+3/D+7 extension watch after a deadline.
Historical baselines from the Internet Archive are labelled `WAYBACK_BASELINE` and never mixed with live changes.

## 8. Results (Run 1 + Run 2, 13 Nov 2026) and known limitations

| Metric (minimum) | Result |
|---|---|
| Real scholarship records (20+) | **84** |
| From official/primary sources (15+) | **81** (3 more are provider-match, not yet approved) |
| Confidence ≥ 95 % (10+) | **15**, all passing every gate |
| Source types (≥ 3) | 7: central govt, state govt, regulator, university, CSR, foundation/trust, international |
| Change detection (≥ 2) | 47 historical changes vs archived official pages (e.g. Sightsavers deadline 20→31 Jul 2026; NSP cycle 31-10-2025 → 30-11-2026); live changes accrue from daily runs |
| Expired / stale (≥ 2) | 13 EXPIRED (e.g. Santoor closed 15-10-2026, LIC Golden Jubilee 22-12-2024); lapsed domain `wbmdfcscholarship.org` caught by the drift rule |
| Discovery beyond seeds | 63 sources discovered at run time (outlinks + aggregator leads) |
| Run 2 (repeat run) | 0 false changes; 10 UNCHANGED, 1 NEW |

Accuracy work done on real pages before release (each now a regression test): site-banner dates no longer taken as
scheme deadlines; reservation/quota/definition sentences no longer read as gender or category criteria; "not
eligible" lists excluded; prerequisite phrases ("must pass Diploma") not read as education level; distinct schemes
(pre/post-matric, UG/PG, degree/diploma) never merged; one guideline PDF shared by several schemes is never used for
field values; OCR-garbled PDF text layers are rejected. Invariant tests run over the real DB on every `pytest`.

**Limitations:** JS-only portals (myScheme, Reliance Foundation) need the optional scrapy-playwright; scanned PDFs
are skipped, not OCR'd; many state portals are login-gated (not crawled by design); foreign scholarships are kept only
when the page itself names Indian eligibility, so recall there is deliberately low.
