"""Configuration loading (YAML) and project paths."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("ATLAS_DATA_DIR", ROOT / "data"))
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "atlas.db"
DB_URL = os.environ.get("ATLAS_DB_URL", f"sqlite:///{DB_PATH.as_posix()}")

USER_AGENT = os.environ.get(
    "ATLAS_USER_AGENT",
    "AtlasScholarshipBot/0.1 (educational scholarship-verification crawler; obeys robots.txt)",
)


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=None)
def keywords() -> dict:
    return _load("keywords.yaml")


@lru_cache(maxsize=None)
def domains() -> dict:
    return _load("domains.yaml")


@lru_cache(maxsize=None)
def scoring() -> dict:
    return _load("scoring.yaml")


@lru_cache(maxsize=None)
def frequency() -> dict:
    return _load("frequency.yaml")


@lru_cache(maxsize=None)
def sources() -> list[dict]:
    raw = _load("sources.yaml")
    defaults = raw.get("defaults", {})
    out = []
    for s in raw.get("sources", []):
        merged = {**defaults, **s}
        merged.setdefault("domain_aliases", [])
        merged.setdefault("sitemaps", [])
        out.append(merged)
    return out


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
