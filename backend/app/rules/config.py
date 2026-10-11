"""Loads and validates the YAML rule files once at import time."""
from functools import lru_cache
from pathlib import Path

import yaml

RULES_DIR = Path(__file__).parent
PRIORITY_ORDER = {"LOW": 0, "MEDIUM": 1, "URGENT": 2}
FALLBACK_CATEGORY = "other_needs_review"
FALLBACK_SUBCATEGORY = "unclassified"


def _load(name: str) -> dict:
    with open(RULES_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def taxonomy() -> dict:
    data = _load("taxonomy.yaml")
    assert FALLBACK_CATEGORY in data["categories"], "taxonomy must define the fallback category"
    return data


@lru_cache
def routing() -> dict:
    data = _load("routing.yaml")
    teams = set(data["teams"])
    for cat in taxonomy()["categories"]:
        team = data["category_team"].get(cat)
        assert team in teams, f"routing.yaml: category {cat!r} has no valid team"
    for p in PRIORITY_ORDER:
        assert p in data["sla_hours"], f"routing.yaml: missing SLA for {p}"
    return data


@lru_cache
def priority_rules() -> dict:
    data = _load("priority_rules.yaml")
    assert data["guards"]["inquiry_max_priority_without_rule"] in PRIORITY_ORDER
    return data


def category_keys() -> list[str]:
    return list(taxonomy()["categories"].keys())


def subcategory_keys(category: str) -> list[str]:
    return list(taxonomy()["categories"][category]["subcategories"].keys())


def is_valid_classification(category: str, subcategory: str) -> bool:
    cats = taxonomy()["categories"]
    return category in cats and subcategory in cats[category]["subcategories"]


def category_label(category: str | None) -> str:
    if not category:
        return ""
    return taxonomy()["categories"].get(category, {}).get("label", category)


def team_for_category(category: str) -> str:
    return routing()["category_team"].get(category, routing()["category_team"][FALLBACK_CATEGORY])


def teams() -> list[str]:
    return list(routing()["teams"])


def sla_hours(priority: str) -> int:
    return int(routing()["sla_hours"][priority])
