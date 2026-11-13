"""Scholarship Intelligence — Streamlit dashboard (read-only view over data/atlas.db)."""
from __future__ import annotations

import html
import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("ATLAS_DB_PATH", ROOT / "data" / "atlas.db"))

st.set_page_config(page_title="Scholarship Intelligence", page_icon="🎓", layout="wide")

STATUS_COLORS = {"ACTIVE": "#1a7f37", "EXPIRING_SOON": "#b35900", "EXPIRED": "#6e7781",
                 "REVIEW_REQUIRED": "#9a6700", "NO_LONGER_VERIFIABLE": "#cf222e"}
NS = "Not specified"


@st.cache_resource
def conn():
    c = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


@st.cache_data(ttl=60)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn(), params=params)


def badge(text: str, color: str) -> str:
    return (f"<span style='background:{color};color:white;padding:2px 8px;border-radius:10px;"
            f"font-size:0.8em;font-weight:600'>{html.escape(text)}</span>")


def ns(v, fmt=None):
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" or v == "[]":
        return f"_{NS}_"
    return fmt(v) if fmt else str(v)


def val(x):
    """None for SQL NULL / pandas NaN (NaN is truthy, so never test raw cells directly)."""
    if x is None or (isinstance(x, float) and pd.isna(x)) or x == "":
        return None
    return x


def fmt_amount(rec) -> str:
    """'₹50,000 per year' / '₹10,000 – ₹20,000 per year' / 'Not specified' (from evidenced values only)."""
    lo, hi, per = val(rec["amount_min_inr"]), val(rec["amount_max_inr"]), val(rec["amount_period"])
    if lo is None:
        return NS
    if hi is not None and hi != lo and not per:
        # several benefit components with different periods (e.g. fee ceiling p.a. + living allowance p.m.)
        return f"Multiple components: {inr(lo)} – {inr(hi)} (see details)"
    s = inr(lo) if lo == hi or hi is None else f"{inr(lo)} – {inr(hi)}"
    if per:
        s += {"YEAR": " per year", "MONTH": " per month", "SEMESTER": " per semester",
              "ONE_TIME": " one-time"}.get(per, f" per {str(per).lower()}")
    return s


def details_url(rec) -> str:
    """Where a user reads the full scheme: the official guideline/specification document when the scheme
    is listed as a card on a portal (e.g. NSP), otherwise the scheme's own official page."""
    return val(rec["detail_doc_url"]) or rec["official_source_url"]


def inr(v):
    """Indian digit grouping: 372000 -> ₹3,72,000."""
    n = int(v)
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{'-' if n < 0 else ''}₹{s}"


if not DB.exists():
    st.error(f"Database not found at {DB}. Run `python -m atlas.cli run` first.")
    st.stop()

page = st.sidebar.radio("View", ["Overview", "Scholarships", "Changes", "Crawl runs", "Sources & discovery"])
st.sidebar.caption(f"DB: {DB.name} · refreshed {datetime.now():%H:%M:%S}")
st.sidebar.markdown("Confidence is computed from evidence (9 signals + 5 gates). "
                    "**VERIFIED** only at ≥ 95% with all gates passing.")

S = q("SELECT * FROM scholarships WHERE is_active_record = 1")

# ============================================================================ OVERVIEW
if page == "Overview":
    st.title("🎓 Scholarship Intelligence")
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat(sep=" ")
    recent = q("SELECT COUNT(DISTINCT scholarship_id) n FROM field_changes WHERE origin='LIVE' AND detected_at >= ?",
               (week_ago,))["n"][0]
    k = st.columns(5)
    k[0].metric("Total discovered", len(S))
    k[1].metric("Verified", int((S.verification_status == "VERIFIED").sum()))
    k[2].metric("Review required", int((S.verification_status != "VERIFIED").sum()))
    k[3].metric("Average confidence", f"{S.confidence.mean():.1f}%" if len(S) else "–")
    k[4].metric("Recently updated (7d)", int(recent))
    k2 = st.columns(5)
    for col, stt in zip(k2, ["ACTIVE", "EXPIRING_SOON", "EXPIRED", "REVIEW_REQUIRED", "NO_LONGER_VERIFIABLE"]):
        col.metric(stt.replace("_", " ").title(), int((S.status == stt).sum()))

    if len(S):
        c1, c2 = st.columns(2)
        by_type = S.groupby(["source_type", "verification_status"]).size().reset_index(name="count")
        c1.plotly_chart(px.bar(by_type, x="source_type", y="count", color="verification_status",
                               title="Records by official source type",
                               color_discrete_map={"VERIFIED": "#1a7f37", "REVIEW_REQUIRED": "#9a6700"}),
                        width="stretch")
        fig = px.histogram(S, x="confidence", nbins=20, title="Confidence distribution")
        fig.add_vline(x=95, line_dash="dash", line_color="#cf222e", annotation_text="VERIFIED ≥ 95")
        c2.plotly_chart(fig, width="stretch")
        c3, c4 = st.columns(2)
        st_counts = S.status.value_counts().reset_index()
        st_counts.columns = ["status", "count"]
        c3.plotly_chart(px.pie(st_counts, names="status", values="count", title="Lifecycle status",
                               color="status", color_discrete_map=STATUS_COLORS), width="stretch")
        soon = S[S.closing_date.notna()].copy()
        soon["closing_date"] = pd.to_datetime(soon.closing_date).dt.date
        soon = soon[(soon.closing_date >= date.today()) & (soon.closing_date <= date.today() + timedelta(days=30))]
        c4.markdown("**Deadlines in the next 30 days**")
        c4.dataframe(soon.sort_values("closing_date")[["name", "closing_date", "confidence", "status"]],
                     hide_index=True, width="stretch")

    runs = q("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1")
    if len(runs):
        r = runs.iloc[0]
        st.info(f"**Last run #{r.id}** ({r.trigger}) {r.started_at} → {r.finished_at or 'running'} · "
                f"pages fetched {r.pages_fetched} · new {r.new_count} · changed {r.changed_count} · "
                f"unchanged {r.unchanged_count} · missing {r.missing_count} · expired {r.expired_count}")

# ============================================================================ SCHOLARSHIPS
elif page == "Scholarships":
    st.title("Scholarships")
    c = st.columns([3, 1, 1, 1])
    term = c[0].text_input("Search name / provider / eligibility", "")
    f_ver = c[1].multiselect("Verification", ["VERIFIED", "REVIEW_REQUIRED"])
    f_status = c[2].multiselect("Status", list(STATUS_COLORS))
    f_type = c[3].multiselect("Source type", sorted(S.source_type.dropna().unique()))
    c = st.columns([1, 1, 1, 1])
    f_gender = c[0].multiselect("Gender", sorted(S.gender.dropna().unique()))
    f_level = c[1].multiselect("Education level", ["PRE_MATRIC", "POST_MATRIC", "CLASS_9_10", "CLASS_11_12",
                                                   "DIPLOMA_ITI", "UG", "PG", "PHD", "POSTDOC"])
    f_cat = c[2].multiselect("Category", ["SC", "ST", "OBC", "EWS", "MINORITY", "PWD", "DEFENCE", "SPORTS"])
    min_conf = c[3].slider("Min confidence", 0, 100, 0)

    df = S.copy()
    if term.strip():
        try:
            ids = q("SELECT rowid FROM scholarships_fts WHERE scholarships_fts MATCH ?",
                    (" ".join(f'"{t}"*' for t in term.split()),))["rowid"].tolist()
            df = df[df.id.isin(ids)]
        except Exception:  # noqa: BLE001 - FTS not built: fall back to LIKE
            m = df.name.str.contains(term, case=False, na=False) | df.provider.str.contains(term, case=False, na=False)
            df = df[m]
    if f_ver:
        df = df[df.verification_status.isin(f_ver)]
    if f_status:
        df = df[df.status.isin(f_status)]
    if f_type:
        df = df[df.source_type.isin(f_type)]
    if f_gender:
        df = df[df.gender.isin(f_gender)]
    if f_level:
        df = df[df.education_levels.fillna("").apply(lambda s: any(lv in s for lv in f_level))]
    if f_cat:
        df = df[df.categories.fillna("").apply(lambda s: any(cat in s for cat in f_cat))]
    df = df[df.confidence >= min_conf].sort_values(["confidence", "name"], ascending=[False, True])

    df = df.assign(details_url=[details_url(r) for _, r in df.iterrows()],
                   amount=[fmt_amount(r) for _, r in df.iterrows()])
    show = df[["id", "name", "provider", "source_type", "closing_date", "status", "verification_status",
               "confidence", "amount", "details_url", "official_source_url"]]
    st.caption(f"{len(df)} records · **View details** opens the official scheme document (guidelines PDF) when the "
               "scheme is listed on a portal, otherwise the scheme's own official page")
    ev = st.dataframe(show, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row",
                      column_config={"amount": st.column_config.TextColumn(
                                         "Amount", help="Scholarship amount/benefit as stated on the official source "
                                                        "(Not specified = not stated there)"),
                                     "details_url": st.column_config.LinkColumn("Scheme details",
                                                                                 display_text="View details ↗"),
                                     "official_source_url": st.column_config.LinkColumn("Official source"),
                                     "confidence": st.column_config.ProgressColumn("Confidence", min_value=0,
                                                                                   max_value=100, format="%.1f%%")})
    sel = ev.selection.rows if ev and ev.selection else []
    if not len(df):
        st.stop()
    options = [f"#{r.id} · {r['name'][:110]}" for _, r in df.iterrows()]
    pick = st.selectbox("Open scholarship (or click a row above)", options, index=sel[0] if sel else 0)
    rec = df.iloc[options.index(pick)]
    sid = int(rec.id)

    st.divider()
    st.header(rec["name"])
    st.markdown(f"{badge(rec.status, STATUS_COLORS.get(rec.status, '#555'))} "
                f"{badge(rec.verification_status, '#1a7f37' if rec.verification_status == 'VERIFIED' else '#9a6700')} "
                f"&nbsp; **Confidence {rec.confidence:.1f}%** · provider: {html.escape(str(rec.provider or NS))}",
                unsafe_allow_html=True)
    b1, b2, b3, _ = st.columns([1.3, 1.1, 1.1, 2.5])
    has_doc = bool(val(rec.detail_doc_url))
    b1.link_button("📄 View scheme details", details_url(rec), type="primary", width="stretch",
                   help="Official scheme guidelines document" if has_doc else "Official scheme page")
    b2.link_button("🌐 Official source", rec.official_source_url, width="stretch",
                   help="Page where the crawler found and verified this scheme")
    if val(rec.application_url):
        b3.link_button("📝 Apply", rec.application_url, width="stretch", help="Application route found on the official page")
    if has_doc:
        st.caption(f"Scheme details: official guidelines document `{rec.detail_doc_url.rsplit('/', 1)[-1]}` "
                   f"linked from the scheme's entry on {rec.official_source_url}")

    E = q("SELECT * FROM field_evidence WHERE scholarship_id=? AND is_current=1 ORDER BY id", (sid,))
    evmap: dict[str, list] = {}
    for _, e in E.iterrows():
        evmap.setdefault(e.field_name, []).append(e)

    def jl(v):
        try:
            x = json.loads(v) if isinstance(v, str) else v
            return ", ".join(map(str, x)) if isinstance(x, list) else x
        except (TypeError, ValueError):
            return v

    amount = NS
    if pd.notna(rec.amount_min_inr):
        amount = inr(rec.amount_min_inr) if rec.amount_min_inr == rec.amount_max_inr else \
            f"{inr(rec.amount_min_inr)} – {inr(rec.amount_max_inr)}"
        if val(rec.amount_period):
            amount += f" per {rec.amount_period.lower().replace('_', ' ')}"
    rows = [
        ("Amount / benefit", amount, "amount"),
        ("Education level", ns(jl(rec.education_levels)), "education_levels"),
        ("Academic requirement", ns(rec.min_percentage, lambda v: f"≥ {v}%") if pd.notna(rec.min_percentage)
         else ns(rec.min_cgpa, lambda v: f"CGPA ≥ {v}"), "min_percentage"),
        ("Income limit", ns(rec.income_limit_inr, inr), "income_limit"),
        ("Age", ns(None if pd.isna(rec.age_min) and pd.isna(rec.age_max) else
                   f"{'' if pd.isna(rec.age_min) else int(rec.age_min)}–{'' if pd.isna(rec.age_max) else int(rec.age_max)}"), "age"),
        ("Gender", ns(rec.gender), "gender"),
        ("Category", ns(jl(rec.categories)), "categories"),
        ("Disability", ns(rec.disability_min_percent, lambda v: f"≥ {int(v)}%"), "disability_min_percent"),
        ("Domicile / state", ns(jl(rec.domicile_states)), "domicile_states"),
        ("Institution requirement", ns(rec.institution_text), "institution_text"),
        ("Opening date", ns(rec.opening_date), "opening_date"),
        ("Closing date", ns(rec.closing_date), "closing_date"),
        ("Academic year / cycle", ns(rec.academic_year), "academic_year"),
        ("Application", ns(rec.application_mode), "application_url"),
        ("Last verified", ns(rec.last_verified_at), None),
        ("Last checked", ns(rec.last_checked_at), None),
    ]
    c1, c2 = st.columns([3, 2])
    with c1:
        st.subheader("Scholarship details")
        st.markdown("\n".join(f"- **{lab}:** {val}" + (" 🔗" if f and f in evmap else "") for lab, val, f in rows))
        for lab, col, f in [("Eligibility", rec.eligibility_text, "eligibility_text"),
                            ("Documents required", rec.documents_required, "documents_required"),
                            ("Selection process", rec.selection_process, "selection_process"),
                            ("Renewal", rec.renewal_text, "renewal_text")]:
            with st.expander(f"{lab}" + (" 🔗" if f in evmap else f" — {NS}")):
                st.text(col if isinstance(col, str) and col else NS)
    with c2:
        st.subheader("Why this score?")
        bd = json.loads(rec.confidence_breakdown or "{}")
        if bd:
            sig = pd.DataFrame(bd["signals"])
            sig["points"] = sig.apply(lambda r: f"{r.points:.1f} / {r.weight:.0f}", axis=1)
            st.dataframe(sig[["id", "label", "points", "reason"]], hide_index=True, width="stretch")
            gates = " ".join(f"{g['id']} {'✅' if g['passed'] else '❌'}" for g in bd["gates"])
            st.markdown(f"**Gates:** {gates}")
            if bd.get("penalty"):
                st.markdown(f"**Conflict penalty:** −{bd['penalty']} ({'; '.join(bd['conflicts'])})")
            st.caption(f"raw score {bd['raw_score']}" + (" → capped at 94.9 because a gate failed" if bd["capped"] else "")
                       + f" · scoring v{bd['scoring_version']}")

    st.subheader("Change history")
    ch = q("SELECT detected_at, field_name, old_value_json AS old_value, new_value_json AS new_value, change_kind, "
           "origin, old_source_url, source_url, old_evidence_text, new_evidence_text FROM field_changes "
           "WHERE scholarship_id=? ORDER BY detected_at DESC", (sid,))
    if len(ch):
        for _, c in ch.iterrows():
            tag = "🕰️ historical baseline (Wayback)" if c.origin == "WAYBACK_BASELINE" else "🔔 CHANGE DETECTED"
            if c.change_kind == "STATUS":
                tag = "🔁 status change"
            st.markdown(f"**{tag}** · `{c.field_name}` · {c.detected_at}  \n"
                        f"old: `{c.old_value}` → new: `{c.new_value}`")
            if c.old_evidence_text or c.new_evidence_text:
                st.caption(f"old evidence ({c.old_source_url or '-'}): {str(c.old_evidence_text or '-')[:300]}")
                st.caption(f"new evidence ({c.source_url}): {str(c.new_evidence_text or '-')[:300]}")
    else:
        st.caption("No changes recorded yet (first seen "
                   f"{rec.first_seen_at}; re-checked on every run).")

    st.subheader("Source evidence")
    st.caption("Database → official source → evidence snippet (verbatim from the stored snapshot) → extracted value")
    for _, e in E.iterrows():
        val = jl(e.value_json)
        snippet = html.escape(str(e.evidence_text)[:600])
        sv = html.escape(str(val)) if not isinstance(val, dict) else ""
        if sv and len(sv) < 60 and sv in snippet:
            snippet = snippet.replace(sv, f"<mark>{sv}</mark>", 1)
        loc = json.loads(e.locator or "{}")
        where = f"PDF page {loc['pdf_page']}" if "pdf_page" in loc else (f"chars {loc['char']}" if "char" in loc else "")
        st.markdown(f"**{e.field_name}** = `{str(val)[:120]}` {'✅' if e.trace_ok else '⚠️'}  \n"
                    f"<div style='border-left:3px solid #8c959f;padding-left:8px;font-size:0.9em'>{snippet}</div>"
                    f"<small>source: <a href='{e.source_url}'>{html.escape(e.source_url[:90])}</a> · {e.evidence_kind} · "
                    f"{e.extractor} · {where} · snapshot {str(e.snapshot_sha256)[:12]}</small>", unsafe_allow_html=True)

    srcs = q("SELECT role, url, registered_domain, is_official, first_seen_at, last_seen_at FROM scholarship_sources "
             "WHERE scholarship_id=?", (sid,))
    st.subheader("Source pages")
    st.dataframe(srcs, hide_index=True, width="stretch",
                 column_config={"url": st.column_config.LinkColumn("URL")})

# ============================================================================ CHANGES
elif page == "Changes":
    st.title("Change feed")
    ch = q("SELECT c.detected_at, s.name, c.field_name, c.old_value_json AS old_value, c.new_value_json AS new_value, "
           "c.change_kind, c.origin, c.run_id, c.source_url, c.old_source_url FROM field_changes c "
           "JOIN scholarships s ON s.id=c.scholarship_id ORDER BY c.detected_at DESC")
    origin = st.multiselect("Origin", ["LIVE", "WAYBACK_BASELINE"], default=["LIVE", "WAYBACK_BASELINE"])
    kinds = st.multiselect("Kind", ["UPDATED", "ADDED", "REMOVED", "STATUS"], default=["UPDATED", "ADDED", "REMOVED", "STATUS"])
    ch = ch[ch.origin.isin(origin) & ch.change_kind.isin(kinds)]
    st.caption("LIVE = detected between two crawls of the official page · WAYBACK_BASELINE = archived official page "
               "(Internet Archive) vs current page")
    st.dataframe(ch, hide_index=True, width="stretch",
                 column_config={"source_url": st.column_config.LinkColumn(), "old_source_url": st.column_config.LinkColumn()})

# ============================================================================ RUNS
elif page == "Crawl runs":
    st.title("Crawl runs")
    runs = q("SELECT id, trigger, mode, status, started_at, finished_at, pages_fetched, pages_not_modified, errors, "
             "new_count, changed_count, unchanged_count, missing_count, expired_count FROM crawl_runs ORDER BY id DESC")
    st.dataframe(runs, hide_index=True, width="stretch")
    if len(runs):
        rid = st.selectbox("Run", runs.id.tolist())
        evs = q("SELECT e.event, s.name, e.detail_json, e.created_at FROM run_events e LEFT JOIN scholarships s "
                "ON s.id=e.scholarship_id WHERE e.run_id=? ORDER BY e.event, s.name", (int(rid),))
        st.markdown(" · ".join(f"**{k}** {v}" for k, v in evs.event.value_counts().items()))
        st.dataframe(evs, hide_index=True, width="stretch")
        stats = q("SELECT stats_json FROM crawl_runs WHERE id=?", (int(rid),))["stats_json"][0]
        with st.expander("Raw run statistics"):
            st.json(json.loads(stats or "{}"))

# ============================================================================ SOURCES
else:
    st.title("Sources & discovery")
    src = q("SELECT registry_key, name, registered_domain, source_type, trust_tier, is_official, role, origin, "
            "approval_status, approval_reason, last_crawled_at, consecutive_failures, paused_until FROM sources "
            "ORDER BY origin, source_type, name")
    c = st.columns(3)
    c[0].metric("Seed sources", int((src.origin == "SEED").sum()))
    c[1].metric("Discovered via outlinks", int((src.origin == "DISCOVERED_OUTLINK").sum()))
    c[2].metric("Discovered via aggregator leads", int((src.origin == "DISCOVERED_AGGREGATOR").sum()))
    st.dataframe(src, hide_index=True, width="stretch")
    pk = q("SELECT page_kind, COUNT(*) n FROM pages GROUP BY page_kind ORDER BY n DESC")
    st.plotly_chart(px.bar(pk, x="page_kind", y="n", title="Crawled pages by classification"), width="stretch")
    st.subheader("Aggregator leads (discovery only — never used as data)")
    leads = q("SELECT status, resolution_method, lead_name, outbound_domain, resolved_official_url, found_on_url, note "
              "FROM discovery_leads ORDER BY status, lead_name")
    st.dataframe(leads, hide_index=True, width="stretch",
                 column_config={"resolved_official_url": st.column_config.LinkColumn(),
                                "found_on_url": st.column_config.LinkColumn()})
