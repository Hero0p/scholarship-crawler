"""Traceability: Database -> Official Source -> Evidence -> Extracted Value."""
from __future__ import annotations

import html as _html
import json
from urllib.parse import urlsplit

from atlas import config, snapshots
from atlas.db.models import FieldEvidence


def verify_trace(ev: FieldEvidence, text: str | None = None, raw: bytes | None = None) -> bool:
    """Re-open the stored snapshot and confirm the evidence is really there."""
    kind = ev.evidence_kind
    if kind == "REGISTRY":
        val = json.loads(ev.value_json)
        return any(s.get("provider") == val for s in config.sources())
    if kind == "URL":
        return bool(ev.snapshot_sha256) and snapshots.raw_path(ev.snapshot_sha256).exists()
    if text is None and ev.snapshot_sha256:
        text = snapshots.load_text(ev.snapshot_sha256)
    if kind == "TEXT":
        if not text or not ev.evidence_text:
            return False
        if ev.evidence_text not in text:
            return False
        loc = json.loads(ev.locator or "{}")
        if "char" in loc:
            s, e = loc["char"]
            return text[s:e] == ev.evidence_text
        return True
    if kind == "LINK":
        if raw is None and ev.snapshot_sha256:
            raw = snapshots.load_raw(ev.snapshot_sha256)
        if not raw:
            return False
        html = _html.unescape(raw.decode("utf-8", "replace"))
        url = json.loads(ev.value_json).get("url", "")
        parts = urlsplit(url)
        path_q = parts.path + (("?" + parts.query) if parts.query else "")
        return url in html or (len(path_q) > 1 and path_q in html) or (parts.netloc and parts.netloc in html
                                                                        and parts.path in html)
    return False
