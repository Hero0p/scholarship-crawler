"""Human-readable live trace of what the crawler is doing (enabled with ATLAS_TRACE=1).

Every pipeline step prints one line, e.g.

    02:00:14 FETCH     200  scholarships.gov.in/All-Scholarships          [nsp, depth 1]
    02:00:31 CLASSIFY  LISTING_CARDS  30 scheme cards on scholarships.gov.in/All-Scholarships
    02:00:31 EXTRACT   AICTE - Pragati Scholarship ... | closing 2026-11-30 <- "Student Application Open till : 30-11-2026"
    02:00:32 VERIFY    98.0  VERIFIED  AICTE - Pragati Scholarship ...
    02:00:33 CHANGE    PM YASASVI ...: closing_date 2026-11-30 -> 2026-12-15

Lines go to the console (also from the Scrapy subprocesses, whose stdout is inherited) and are appended to
the run's trace file (ATLAS_TRACE_FILE, default data/logs/trace.log).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

STAGES = ("RUN", "STEP", "FETCH", "RECHECK", "FOLLOW", "DISCOVER", "LEAD", "RESOLVE", "CLASSIFY", "SOURCE",
          "EXTRACT", "EVIDENCE", "REJECT", "STORE", "CHANGE", "ENRICH", "VERIFY", "STATUS", "MISSING", "SUMMARY")


def enabled() -> bool:
    return os.environ.get("ATLAS_TRACE") == "1"


def short(url: str, n: int = 70) -> str:
    u = url.split("://", 1)[-1]
    if u.startswith("www."):
        u = u[4:]
    return u if len(u) <= n else u[: n - 3] + "..."


def clip(text: str, n: int = 90) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 3] + "..."


def trace(stage: str, msg: str) -> None:
    if not enabled():
        return
    line = f"{time.strftime('%H:%M:%S')} {stage:<9} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # Windows console code pages without ₹ etc.
        print(line.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii"),
              flush=True)
    path = os.environ.get("ATLAS_TRACE_FILE")
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
