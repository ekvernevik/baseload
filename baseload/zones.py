"""Single source of truth for bidding-zone configuration.

Zone codes, interconnector pairs, and NTC (net transfer capacity) limits are
config data, not code. Every pipeline module should derive them from a
``cfg`` dict loaded from a YAML config (typically ``configs/demo.yaml``) via
the helpers below, rather than hardcoding its own zone list.

``DEFAULT_ZONES`` / ``DEFAULT_NTC_MW`` describe the current Norwegian 5-zone,
6-interconnector topology (NTC values sourced from Statnett / ENTSO-E Winter
Outlook publications) and serve as the fallback when a config omits the
corresponding key, preserving today's behaviour unchanged.
"""
from __future__ import annotations

DEFAULT_ZONES: list[str] = ["NO1", "NO2", "NO3", "NO4", "NO5"]

#: Symmetric NTC capacity limits per internal zone pair (MW).
#: Source: ENTSO-E Winter Outlook / Statnett published transfer capacities.
DEFAULT_NTC_MW: dict[str, float] = {
    "NO1-NO2": 3500,
    "NO1-NO3": 500,
    "NO1-NO5": 1000,
    "NO2-NO5": 600,
    "NO3-NO4": 700,
    "NO3-NO5": 1000,
}

DEFAULT_PAIRS: list[str] = list(DEFAULT_NTC_MW)


def pair_name(zone_a: str, zone_b: str) -> str:
    """Canonicalize a zone pair as "lower-higher" (lexicographic), e.g. "NO1-NO2"."""
    return f"{min(zone_a, zone_b)}-{max(zone_a, zone_b)}"


def zones_from_cfg(cfg: dict) -> list[str]:
    """Return the configured zone list, falling back to ``DEFAULT_ZONES``."""
    return cfg.get("zones") or DEFAULT_ZONES


def pairs_from_cfg(cfg: dict) -> list[str]:
    """Return the configured interconnector pair list, falling back to ``DEFAULT_PAIRS``."""
    return cfg.get("pairs") or DEFAULT_PAIRS


def ntc_from_cfg(cfg: dict) -> dict[str, float]:
    """Return the configured NTC-by-pair dict, falling back to ``DEFAULT_NTC_MW``."""
    return cfg.get("ntc") or DEFAULT_NTC_MW
