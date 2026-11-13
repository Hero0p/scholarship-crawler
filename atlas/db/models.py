"""SQLAlchemy ORM models (mirror of schema in PROJECT_PLAN.md §6)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    registry_key: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    provider_name: Mapped[str | None] = mapped_column(String)
    registered_domain: Mapped[str] = mapped_column(String, index=True)
    domain_aliases: Mapped[str | None] = mapped_column(Text)          # JSON list
    seeds: Mapped[str | None] = mapped_column(Text)                   # JSON list
    sitemaps: Mapped[str | None] = mapped_column(Text)                # JSON list
    source_type: Mapped[str] = mapped_column(String)
    trust_tier: Mapped[int] = mapped_column(Integer)
    is_official: Mapped[bool] = mapped_column(Boolean)
    role: Mapped[str] = mapped_column(String, default="CRAWL")       # CRAWL | DISCOVERY_ONLY
    origin: Mapped[str] = mapped_column(String, default="SEED")
    approval_status: Mapped[str] = mapped_column(String, default="APPROVED")
    approval_reason: Mapped[str | None] = mapped_column(Text)
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    max_depth: Mapped[int] = mapped_column(Integer, default=3)
    page_budget: Mapped[int] = mapped_column(Integer, default=120)
    base_frequency_hours: Mapped[int] = mapped_column(Integer, default=72)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime)
    discovered_from: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    trigger: Mapped[str] = mapped_column(String, default="MANUAL")
    mode: Mapped[str] = mapped_column(String, default="FULL")
    status: Mapped[str] = mapped_column(String, default="RUNNING")
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    pages_not_modified: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    expired_count: Mapped[int] = mapped_column(Integer, default=0)
    stats_json: Mapped[str | None] = mapped_column(Text)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_url: Mapped[str] = mapped_column(String, unique=True)
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), index=True)
    content_type: Mapped[str | None] = mapped_column(String)
    page_kind: Mapped[str | None] = mapped_column(String)      # DETAIL|LISTING|IRRELEVANT|SOFT_404|ERROR
    relevance_score: Mapped[float | None] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    discovered_from_url: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String)
    last_modified: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String)
    latest_snapshot_sha: Mapped[str | None] = mapped_column(String)
    processed_snapshot_sha: Mapped[str | None] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_crawl_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    unchanged_streak: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[int | None] = mapped_column(Integer)


class Snapshot(Base):
    __tablename__ = "snapshots"
    sha256: Mapped[str] = mapped_column(String, primary_key=True)
    page_id: Mapped[int | None] = mapped_column(Integer, index=True)
    url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String)
    raw_path: Mapped[str] = mapped_column(Text)
    text_path: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String, default="LIVE")   # LIVE | WAYBACK | TEST_FIXTURE
    archive_url: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(Integer)


class Scholarship(Base):
    __tablename__ = "scholarships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    funding_type: Mapped[str | None] = mapped_column(String)
    official_source_url: Mapped[str] = mapped_column(Text)
    official_source_page_id: Mapped[int | None] = mapped_column(Integer)
    official_source_tier: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[int | None] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String)
    application_url: Mapped[str | None] = mapped_column(Text)
    application_mode: Mapped[str | None] = mapped_column(String)
    amount_text: Mapped[str | None] = mapped_column(Text)
    amount_min_inr: Mapped[int | None] = mapped_column(Integer)
    amount_max_inr: Mapped[int | None] = mapped_column(Integer)
    amount_period: Mapped[str | None] = mapped_column(String)
    eligibility_text: Mapped[str | None] = mapped_column(Text)
    education_levels: Mapped[str | None] = mapped_column(Text)     # JSON list
    min_percentage: Mapped[float | None] = mapped_column(Float)
    min_cgpa: Mapped[float | None] = mapped_column(Float)
    academic_text: Mapped[str | None] = mapped_column(Text)
    field_values_json: Mapped[str | None] = mapped_column(Text)    # normalised values used for diffing
    income_limit_inr: Mapped[int | None] = mapped_column(Integer)
    income_text: Mapped[str | None] = mapped_column(Text)
    age_min: Mapped[int | None] = mapped_column(Integer)
    age_max: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String)
    categories: Mapped[str | None] = mapped_column(Text)           # JSON list
    disability_min_percent: Mapped[int | None] = mapped_column(Integer)
    domicile_states: Mapped[str | None] = mapped_column(Text)      # JSON list
    institution_text: Mapped[str | None] = mapped_column(Text)
    opening_date: Mapped[date | None] = mapped_column(Date)
    closing_date: Mapped[date | None] = mapped_column(Date)
    deadline_text: Mapped[str | None] = mapped_column(Text)
    is_rolling: Mapped[bool | None] = mapped_column(Boolean)
    academic_year: Mapped[str | None] = mapped_column(String)
    documents_required: Mapped[str | None] = mapped_column(Text)
    selection_process: Mapped[str | None] = mapped_column(Text)
    renewal_text: Mapped[str | None] = mapped_column(Text)
    page_status_text: Mapped[str | None] = mapped_column(Text)
    detail_doc_url: Mapped[str | None] = mapped_column(Text)        # official guideline/spec document
    record_kind: Mapped[str | None] = mapped_column(String)          # PAGE | CARD
    status: Mapped[str] = mapped_column(String, default="REVIEW_REQUIRED")
    verification_status: Mapped[str] = mapped_column(String, default="REVIEW_REQUIRED")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_breakdown: Mapped[str | None] = mapped_column(Text)
    scoring_version: Mapped[str | None] = mapped_column(String)
    india_eligible: Mapped[bool | None] = mapped_column(Boolean)
    discovered_via: Mapped[str | None] = mapped_column(String)     # SEED | DISCOVERED_OUTLINK | DISCOVERED_AGGREGATOR
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)
    live_status: Mapped[str | None] = mapped_column(String)        # LIVE | MISSING | GONE | DRIFTED | UNREACHABLE
    name_match_score: Mapped[float | None] = mapped_column(Float)
    consistency_ratio: Mapped[float | None] = mapped_column(Float)
    is_active_record: Mapped[bool] = mapped_column(Boolean, default=True)


class ScholarshipSource(Base):
    __tablename__ = "scholarship_sources"
    scholarship_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str | None] = mapped_column(Text)
    registered_domain: Mapped[str | None] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)      # PRIMARY | CORROBORATING | DISCOVERY
    is_official: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FieldEvidence(Base):
    __tablename__ = "field_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scholarship_id: Mapped[int] = mapped_column(Integer, index=True)
    field_name: Mapped[str] = mapped_column(String)
    value_json: Mapped[str] = mapped_column(Text)
    evidence_text: Mapped[str] = mapped_column(Text)
    evidence_kind: Mapped[str] = mapped_column(String, default="TEXT")  # TEXT | LINK | REGISTRY | URL
    source_url: Mapped[str] = mapped_column(Text)
    page_id: Mapped[int | None] = mapped_column(Integer, index=True)   # page that owns this field value
    snapshot_sha256: Mapped[str | None] = mapped_column(String)
    locator: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String)
    extractor: Mapped[str | None] = mapped_column(String)
    extraction_pass: Mapped[str | None] = mapped_column(String)
    trace_ok: Mapped[bool | None] = mapped_column(Boolean)
    run_id: Mapped[int | None] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class FieldChange(Base):
    __tablename__ = "field_changes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scholarship_id: Mapped[int] = mapped_column(Integer, index=True)
    field_name: Mapped[str] = mapped_column(String)
    old_value_json: Mapped[str | None] = mapped_column(Text)
    new_value_json: Mapped[str | None] = mapped_column(Text)
    change_kind: Mapped[str] = mapped_column(String)       # UPDATED | ADDED | REMOVED | STATUS
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    run_id: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    old_source_url: Mapped[str | None] = mapped_column(Text)
    old_evidence_id: Mapped[int | None] = mapped_column(Integer)
    new_evidence_id: Mapped[int | None] = mapped_column(Integer)
    old_evidence_text: Mapped[str | None] = mapped_column(Text)
    new_evidence_text: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String, default="LIVE")  # LIVE | WAYBACK_BASELINE


class ScholarshipVersion(Base):
    __tablename__ = "scholarship_versions"
    scholarship_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_json: Mapped[str] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VerificationCheck(Base):
    __tablename__ = "verification_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scholarship_id: Mapped[int] = mapped_column(Integer, index=True)
    run_id: Mapped[int | None] = mapped_column(Integer)
    check_name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)          # SIGNAL | GATE | PENALTY
    weight: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)
    points: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    scholarship_id: Mapped[int | None] = mapped_column(Integer)
    event: Mapped[str] = mapped_column(String)
    detail_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiscoveryLead(Base):
    __tablename__ = "discovery_leads"
    __table_args__ = (UniqueConstraint("found_on_url", "outbound_url"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_name: Mapped[str | None] = mapped_column(Text)
    found_on_url: Mapped[str] = mapped_column(Text)
    aggregator_domain: Mapped[str | None] = mapped_column(String)
    outbound_url: Mapped[str | None] = mapped_column(Text)
    outbound_domain: Mapped[str | None] = mapped_column(String)
    resolved_official_url: Mapped[str | None] = mapped_column(Text)
    resolution_method: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="NEW")
    note: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
