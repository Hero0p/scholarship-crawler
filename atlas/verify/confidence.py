"""Deterministic, evidence-based confidence score (PROJECT_PLAN.md §11).

score = sum(weight_i * value_i) - conflict_penalty ; gates must all pass for VERIFIED (>= 95).
Every signal value comes with a human-readable reason ("Why this score?").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from atlas import config, snapshots
from atlas.db.models import FieldEvidence, Page, Scholarship, Source, VerificationCheck
from atlas.db.session import get_session

STRUCTURED = ["income_limit", "age", "gender", "categories", "disability_min_percent", "min_percentage",
              "min_cgpa", "domicile_states", "education_levels", "institution_text"]


@dataclass
class Signal:
    id: str
    label: str
    weight: float
    value: float
    reason: str

    @property
    def points(self) -> float:
        return round(self.weight * self.value, 2)


def current_academic_start(today: date) -> int:
    return today.year if today.month >= 6 else today.year - 1


def core_name(name: str) -> str:
    """'AICTE - Pragati Scholarship Scheme For Girl Students ( Technical Degree) (Merit Based Scheme)'
    -> 'pragati scholarship scheme for girl students'"""
    n = re.sub(r"\([^)]*\)", " ", name)
    n = re.sub(r"^[A-Z][A-Za-z&.]{1,15}(?:\s[A-Z][A-Za-z&.]{1,15}){0,3}\s+-\s+", "", n.strip())
    n = re.sub(r"\b20\d{2}(?:\s*[-–/]\s*\d{2,4})?\b", " ", n)
    return re.sub(r"\s+", " ", n.lower()).strip(" -:")


def name_match(name: str, text: str | None) -> float:
    if not text:
        return 0.0
    n = re.sub(r"\s+", " ", name.lower()).strip()
    t = text.lower()
    if n in t:
        return 100.0
    head = t[:6000]
    return float(fuzz.partial_ratio(n, head))


class Verifier:
    def __init__(self, s: Session):
        self.s = s
        self.cfg = config.scoring()
        self.w = self.cfg["weights"]
        self.today = date.today()
        self._texts: dict[int, str] = {}
        # corroboration index: official pages (any domain) that we processed
        self.corpus: list[tuple[str, str]] = []   # (registered_domain, lowercase text)
        q = (s.query(Page, Source).join(Source, Page.source_id == Source.id)
             .filter(Source.is_official.is_(True), Page.page_kind.in_(("DETAIL", "LISTING", "DETAIL_REJECTED")),
                     Page.latest_snapshot_sha.isnot(None)))
        for page, src in q:
            txt = snapshots.load_text(page.latest_snapshot_sha)
            if txt:
                self.corpus.append((src.registered_domain, txt.lower()))
        self.records = s.query(Scholarship).filter(Scholarship.is_active_record.is_(True)).all()

    def page_text(self, page: Page | None) -> str | None:
        if not page or not page.latest_snapshot_sha:
            return None
        if page.id not in self._texts:
            self._texts[page.id] = snapshots.load_text(page.latest_snapshot_sha) or ""
        return self._texts[page.id]

    # ------------------------------------------------------------------ signals
    def s1(self, sch: Scholarship, src: Source | None) -> Signal:
        table = self.cfg["source_authority"]
        st = src.source_type if src else "UNKNOWN"
        if src and not src.is_official and st not in ("AGGREGATOR", "NEWS_BLOG"):
            st = "PROVIDER_UNAPPROVED"
        v = table.get(st, 0.0)
        how = f"{src.registered_domain} classified {st} (tier {src.trust_tier}); origin {src.origin}" if src else "no source"
        return Signal("S1", "Official source", self.w["S1_official_source"], v, how)

    def s2(self, sch: Scholarship, page: Page | None) -> Signal:
        w = self.w["S2_live_on_source"]
        if page is None:
            return Signal("S2", "Live on official source", w, 0.0, "official page unknown")
        if page.page_kind in ("GONE", "SOFT_404", "DRIFTED") or (page.http_status or 0) in (404, 410):
            return Signal("S2", "Live on official source", w, 0.0,
                          f"latest check: HTTP {page.http_status}, page {page.page_kind}")
        if page.consecutive_failures:
            return Signal("S2", "Live on official source", w, 0.0,
                          f"latest fetch failed ({page.consecutive_failures} consecutive failures)")
        score = name_match(sch.name, self.page_text(page))
        sch.name_match_score = score
        if score >= self.cfg["name_match_live"]:
            v, why = 1.0, f"HTTP {page.http_status} on {page.last_ok_at:%Y-%m-%d}, name match {score:.0f}"
        elif score >= self.cfg["name_match_partial"]:
            v, why = 0.5, f"page live but name only partially found (match {score:.0f})"
        else:
            v, why = 0.0, f"scholarship name not found on official page (match {score:.0f})"
        if v and page.last_ok_at and datetime.utcnow() - page.last_ok_at > timedelta(days=self.cfg["liveness_decay_days"]):
            v *= 0.5
            why += f"; last successful check older than {self.cfg['liveness_decay_days']} days (x0.5)"
        return Signal("S2", "Live on official source", w, v, why)

    def s3(self, sch: Scholarship, ev: dict) -> Signal:
        w = self.w["S3_application_route"]
        e = ev.get("application_url")
        if not e:
            return Signal("S3", "Official application route", w, 0.0, "no application link found on the official page")
        val = json.loads(e[0].value_json)
        route = val.get("route")
        v = {"OFFICIAL": 1.0, "PLATFORM": 0.8, "THIRD_PARTY": 0.8, "OFFICIAL_FORM": 0.7}.get(route, 0.0)
        return Signal("S3", "Official application route", w, v,
                      f"'{e[0].evidence_text[:90]}' ({route.lower().replace('_', ' ')})")

    def s4(self, sch: Scholarship, ev: dict) -> Signal:
        w = self.w["S4_eligibility_support"]
        n = sum(1 for k in STRUCTURED if k in ev)
        found = [k for k in STRUCTURED if k in ev]
        if n >= 3:
            v = 1.0
        elif n == 2:
            v = 0.8
        elif n == 1:
            v = 0.5
        elif "eligibility_text" in ev:
            v = 0.3
        else:
            v = 0.0
        why = f"{n} structured criteria with evidence ({', '.join(found)})" if n else \
            ("eligibility text only" if v else "no eligibility evidence")
        return Signal("S4", "Eligibility supported", w, v, why)

    def s5(self, sch: Scholarship, ev: dict) -> Signal:
        w = self.w["S5_deadline_support"]
        if "closing_date" in ev:
            return Signal("S5", "Deadline supported", w, 1.0, f"closing date {sch.closing_date} with evidence")
        if "is_rolling" in ev:
            return Signal("S5", "Deadline supported", w, 0.8, "explicitly open throughout the year / rolling")
        if "academic_year" in ev:
            return Signal("S5", "Deadline supported", w, 0.4, f"only cycle known ({sch.academic_year}), no date")
        return Signal("S5", "Deadline supported", w, 0.0, "no deadline on official source")

    def s6(self, sch: Scholarship, page: Page | None) -> Signal:
        w = self.w["S6_currency"]
        recent = self.cfg["currency_recent_days"]
        cur = current_academic_start(self.today)
        if sch.closing_date:
            age = (self.today - sch.closing_date).days
            if age <= recent:
                return Signal("S6", "Information current", w, 1.0, f"closing date {sch.closing_date} is current")
            if age <= 365 + recent:
                return Signal("S6", "Information current", w, 0.4, f"closing date {sch.closing_date} is from the previous cycle")
            return Signal("S6", "Information current", w, 0.1, f"closing date {sch.closing_date} is old")
        if sch.academic_year:
            y1 = int(sch.academic_year[:4])
            if y1 >= cur:
                return Signal("S6", "Information current", w, 1.0, f"current cycle {sch.academic_year} mentioned")
            if y1 == cur - 1:
                return Signal("S6", "Information current", w, 0.4, f"previous cycle {sch.academic_year}")
        if sch.page_status_text == "OPEN":
            return Signal("S6", "Information current", w, 1.0, "official page states applications are open")
        if page and page.last_modified:
            try:
                from email.utils import parsedate_to_datetime
                lm = parsedate_to_datetime(page.last_modified).replace(tzinfo=None)
                if datetime.utcnow() - lm <= timedelta(days=365):
                    return Signal("S6", "Information current", w, 1.0, f"Last-Modified {lm:%Y-%m-%d}")
            except (TypeError, ValueError):
                pass
        return Signal("S6", "Information current", w, 0.1, "no date evidence of currency")

    def s7(self, sch: Scholarship, rows: list[FieldEvidence]) -> Signal:
        w = self.w["S7_traceability"]
        if not rows:
            return Signal("S7", "Fields traceable to evidence", w, 0.0, "no evidence rows")
        ok = sum(1 for r in rows if r.trace_ok)
        return Signal("S7", "Fields traceable to evidence", w, ok / len(rows),
                      f"{ok}/{len(rows)} evidence snippets re-verified verbatim in stored snapshot")

    def s8(self, sch: Scholarship) -> Signal:
        w = self.w["S8_consistency"]
        r = sch.consistency_ratio
        if r is None:
            return Signal("S8", "Extraction consistency", w, 0.5,
                          "no key field found by both extraction passes (neutral)")
        return Signal("S8", "Extraction consistency", w, r,
                      f"{r:.0%} of key fields identical in section-scoped and full-text passes")

    def s9(self, sch: Scholarship, src: Source | None) -> tuple[Signal, float, list[str]]:
        w = self.w["S9_corroboration"]
        own = src.registered_domain if src else ""
        nm = core_name(sch.name)
        others = set()
        if len(nm) >= 15 and len(nm.split()) >= 3:
            for dom, txt in self.corpus:
                if dom != own and nm in txt:
                    others.add(dom)
        conflicts = []
        for other in self.records:
            if other.id == sch.id or other.source_id == sch.source_id:
                continue
            if fuzz.token_set_ratio(other.name.lower(), sch.name.lower()) >= 95 and \
                    sch.closing_date and other.closing_date and sch.closing_date != other.closing_date and \
                    (sch.academic_year or "") == (other.academic_year or ""):
                conflicts.append(f"closing date {other.closing_date} on {other.official_source_url}")
        penalty = min(self.cfg["conflict_penalty_per_field"] * len(conflicts), self.cfg["conflict_penalty_cap"])
        if others:
            return Signal("S9", "Corroboration", w, 1.0, f"also named on official domain(s): {', '.join(sorted(others)[:3])}"), penalty, conflicts
        if src and src.is_official:
            return Signal("S9", "Corroboration", w, 0.6, "single official source, no conflicts found"), penalty, conflicts
        return Signal("S9", "Corroboration", w, 0.3, "no official corroboration"), penalty, conflicts

    # ------------------------------------------------------------------ score
    def score(self, sch: Scholarship, run_id=None) -> float:
        s = self.s
        src = s.get(Source, sch.source_id) if sch.source_id else None
        page = s.get(Page, sch.official_source_page_id) if sch.official_source_page_id else None
        rows = s.query(FieldEvidence).filter_by(scholarship_id=sch.id, is_current=True).all()
        ev: dict[str, list[FieldEvidence]] = {}
        for r in rows:
            ev.setdefault(r.field_name, []).append(r)
        sigs = [self.s1(sch, src), self.s2(sch, page), self.s3(sch, ev), self.s4(sch, ev), self.s5(sch, ev),
                self.s6(sch, page), self.s7(sch, rows), self.s8(sch)]
        s9, penalty, conflicts = self.s9(sch, src)
        sigs.append(s9)
        raw = round(sum(x.points for x in sigs) - penalty, 1)
        sv = {x.id: x.value for x in sigs}
        gates = [
            ("G1", "Official source", sv["S1"] >= 0.90),
            ("G2", "Live on official source now", sv["S2"] == 1.0),
            ("G3", "All fields traceable", sv["S7"] == 1.0),
            ("G4", "No open conflicts", penalty == 0),
            ("G5", "Name, provider & official URL present", bool(sch.name and sch.provider and sch.official_source_url)),
        ]
        failed = [g for g in gates if not g[2]]
        conf = raw if not failed else min(raw, self.cfg["gate_cap"])
        conf = max(0.0, round(conf, 1))
        verified = conf >= self.cfg["verified_threshold"] and not failed

        s.query(VerificationCheck).filter_by(scholarship_id=sch.id, is_current=True).update({"is_current": False})
        for x in sigs:
            s.add(VerificationCheck(scholarship_id=sch.id, run_id=run_id, check_name=f"{x.id} {x.label}", kind="SIGNAL",
                                    weight=x.weight, value=round(x.value, 3), points=x.points, passed=x.value >= 0.99,
                                    reason=x.reason))
        for gid, label, ok in gates:
            s.add(VerificationCheck(scholarship_id=sch.id, run_id=run_id, check_name=f"{gid} {label}", kind="GATE",
                                    passed=ok, reason="pass" if ok else "FAILED"))
        if penalty:
            s.add(VerificationCheck(scholarship_id=sch.id, run_id=run_id, check_name="P Conflicting official sources",
                                    kind="PENALTY", points=-penalty, passed=False, reason="; ".join(conflicts)))
        sch.confidence = conf
        sch.verification_status = "VERIFIED" if verified else "REVIEW_REQUIRED"
        sch.scoring_version = self.cfg["version"]
        sch.confidence_breakdown = json.dumps({
            "signals": [{"id": x.id, "label": x.label, "weight": x.weight, "value": round(x.value, 3),
                         "points": x.points, "reason": x.reason} for x in sigs],
            "gates": [{"id": g, "label": lab, "passed": ok} for g, lab, ok in gates],
            "penalty": penalty, "conflicts": conflicts, "raw_score": raw,
            "capped": bool(failed), "scoring_version": self.cfg["version"]})
        if verified:
            sch.last_verified_at = page.last_ok_at if page and page.last_ok_at else datetime.utcnow()
        return conf


def verify_all(run_id=None) -> dict:
    with get_session() as s:
        v = Verifier(s)
        n = ver = 0
        for sch in v.records:
            v.score(sch, run_id)
            n += 1
            ver += sch.verification_status == "VERIFIED"
        s.commit()
    return {"scored": n, "verified": ver}
