"""Immutable evidence store: raw bytes keyed by SHA-256, plus derived text."""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from atlas import config


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def raw_path(sha: str) -> Path:
    return config.SNAPSHOT_DIR / sha[:2] / f"{sha}.raw.gz"


def text_path(sha: str) -> Path:
    return config.SNAPSHOT_DIR / sha[:2] / f"{sha}.txt.gz"


def store_raw(data: bytes) -> str:
    sha = sha256(data)
    p = raw_path(sha)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wb") as fh:
            fh.write(data)
    return sha


def load_raw(sha: str) -> bytes | None:
    p = raw_path(sha)
    if not p.exists():
        return None
    with gzip.open(p, "rb") as fh:
        return fh.read()


def store_text(sha: str, text: str) -> Path:
    """Derived text of a raw snapshot (deterministic). Rewritten if the converter's output changed,
    so stored evidence offsets always refer to the current text representation."""
    p = text_path(sha)
    if p.exists() and load_text(sha) == text:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return p


def load_text(sha: str) -> str | None:
    p = text_path(sha)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return fh.read()
