"""Tenant scoring tiers.

A simple 5-tier system derived from a 0-100 score. Used to colour-code the
tenant roster and drive collections prioritisation. Thresholds are set in
code (not DB) because they're an analytical constant — adjusting them is a
deliberate code change, not a runtime toggle.
"""
from django.db import models


class Tier(models.TextChoices):
    PLATINUM = "PLATINUM", "Platinum"  # best payers
    GOLD = "GOLD", "Gold"
    SILVER = "SILVER", "Silver"
    BRONZE = "BRONZE", "Bronze"
    WATCH = "WATCH", "Watchlist"  # chronic defaulters


# Inclusive lower bound — score >= threshold falls into this tier.
# Order matters: descending.
TIER_THRESHOLDS = [
    (90, Tier.PLATINUM),
    (75, Tier.GOLD),
    (60, Tier.SILVER),
    (40, Tier.BRONZE),
    (0, Tier.WATCH),
]


TIER_COLOURS = {
    Tier.PLATINUM: "#6b7bff",
    Tier.GOLD: "#d4a017",
    Tier.SILVER: "#8a8f99",
    Tier.BRONZE: "#a05a2c",
    Tier.WATCH: "#c0392b",
}


def tier_for_score(score):
    """Return the Tier label for a given score (int 0-100)."""
    if score is None:
        return Tier.WATCH
    try:
        s = int(score)
    except (TypeError, ValueError):
        return Tier.WATCH
    for threshold, label in TIER_THRESHOLDS:
        if s >= threshold:
            return label
    return Tier.WATCH
