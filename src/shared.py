"""Domain helpers shared by the website and Discord bot.

This module must not import Discord or FastAPI so either service can start and
deploy without loading the other application's runtime.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import replace

from src.cache import SQLiteCache
from src.sources.base import MiningLocationResult, MiningSystemLocations


CZ_TIMERS_CACHE_KEY = "cz:dashboard:timers"
EXEC_OVERRIDE_CACHE_KEY = "exec:cycle-start-override"
MINING_COMMUNITY_LOCATIONS_CACHE_KEY = "mining:community-locations:v1"
MINING_EXCLUDED_LOCATIONS_CACHE_KEY = "mining:excluded-locations:v1"
MINING_LOCATION_TYPES = ("lagrange_points", "planets", "moons", "points_of_interest")
CZ_TIMER_DEFINITIONS = {
    "blue_keycard": ("Blue Keycards", 15 * 60),
    "compboard": ("Compboards / Tablets", 30 * 60),
    "red_keycard": ("Red Keycards", 30 * 60),
    "timer_door": ("Timer Doors", 20 * 60),
}


async def get_cz_dashboard_timers(cache: SQLiteCache) -> dict:
    timers = await cache.get(CZ_TIMERS_CACHE_KEY)
    return timers if isinstance(timers, dict) else {}


def _normalize_text(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def _has_mining_multi_separator(value: str) -> bool:
    return bool(re.search(r"\s*(,|;|\+|&|\band\b)\s*", value, flags=re.IGNORECASE))


def _mining_multi_search_terms(value: str) -> list[str]:
    if not _has_mining_multi_separator(value):
        return [value.strip()] if value.strip() else []
    return [
        term.strip()
        for term in re.split(r"\s*(?:,|;|\+|&|\band\b)\s*", value, flags=re.IGNORECASE)
        if term.strip()
    ]


def _mining_space_search_terms(value: str) -> list[str]:
    return [term.strip() for term in value.split() if term.strip()]


def _mining_term_signatures(result: MiningLocationResult, term: str) -> list[int]:
    signatures = result.rock_signatures or []
    text = str(term or "").replace(",", "").strip()
    signature = int(text) if text.isdigit() else None
    if signature is None:
        return signatures
    return [
        base for base in signatures
        if signature == base or (signature % base == 0 and 1 <= signature // base <= 6)
    ]


def _shared_mining_signatures(signature_groups: list[list[int]]) -> list[int]:
    if not signature_groups or any(not signatures for signatures in signature_groups):
        return []
    shared = set(signature_groups[0])
    for signatures in signature_groups[1:]:
        shared.intersection_update(signatures)
    return sorted(shared)


def _unique_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = _normalize_text(value)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


async def _community_mining_entries(cache: SQLiteCache) -> dict:
    entries = await cache.get(MINING_COMMUNITY_LOCATIONS_CACHE_KEY)
    return entries if isinstance(entries, dict) else {}


async def add_community_mining_location(cache: SQLiteCache, entry: dict) -> None:
    # Adding a corrected report explicitly restores a previously hidden location.
    exclusions = await _mining_exclusions(cache)
    remaining = [item for item in exclusions if not _same_community_mining_location(item, entry)]
    if len(remaining) != len(exclusions):
        await cache.set(MINING_EXCLUDED_LOCATIONS_CACHE_KEY, remaining, 315360000)
    entries = await _community_mining_entries(cache)
    material_entries = entries.setdefault(_normalize_text(entry.get("material")), [])
    new_entry = {
        key: str(entry.get(key) or "").strip()
        for key in ("material", "system", "location_type", "location", "reported_by")
    }
    duplicate = any(
        all(_normalize_text(existing.get(key)) == _normalize_text(new_entry[key])
            for key in ("system", "location_type", "location"))
        for existing in material_entries if isinstance(existing, dict)
    )
    if not duplicate:
        material_entries.append(new_entry)
    await cache.set(MINING_COMMUNITY_LOCATIONS_CACHE_KEY, entries, 315360000)


def _same_community_mining_location(existing: object, entry: dict) -> bool:
    return isinstance(existing, dict) and all(
        _normalize_text(existing.get(field)) == _normalize_text(entry.get(field))
        for field in ("material", "system", "location_type")
    ) and _mining_location_key(existing.get("location")) == _mining_location_key(entry.get("location"))


def _mining_location_key(value: object) -> str:
    text = re.sub(r"\s*\(Community\)\s*$", "", str(value or ""), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def find_mining_location(result: MiningLocationResult, entry: dict) -> str | None:
    kind = entry.get("location_type")
    if kind not in MINING_LOCATION_TYPES:
        return None
    groups = result.location_groups or []
    locations = [location for group in groups
                 if _normalize_text(group.system) == _normalize_text(entry.get("system"))
                 for location in getattr(group, kind)]
    if not groups and len(result.systems) == 1 and _normalize_text(result.systems[0]) == _normalize_text(entry.get("system")):
        locations = getattr(result, kind)
    for location in locations:
        if _mining_location_key(location) == _mining_location_key(entry.get("location")):
            return re.sub(r"\s*\(Community\)\s*$", "", location, flags=re.IGNORECASE).strip()
    return None


async def _mining_exclusions(cache: SQLiteCache) -> list[dict]:
    entries = await cache.get(MINING_EXCLUDED_LOCATIONS_CACHE_KEY)
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


async def remove_mining_location(cache: SQLiteCache, entry: dict) -> bool:
    """Persist a correction so upstream refreshes cannot reintroduce the location."""
    if entry.get("location_type") not in MINING_LOCATION_TYPES or not all(
        str(entry.get(field) or "").strip() for field in ("material", "system", "location")
    ):
        raise ValueError("A material, system, location type, and location are required.")
    exclusions = await _mining_exclusions(cache)
    added = not any(_same_community_mining_location(item, entry) for item in exclusions)
    if added:
        exclusions.append(dict(entry))
        await cache.set(MINING_EXCLUDED_LOCATIONS_CACHE_KEY, exclusions, 315360000)
    removed = await remove_community_mining_location(cache, entry)
    return added or removed


async def _apply_mining_exclusions(cache: SQLiteCache, result: MiningLocationResult) -> MiningLocationResult:
    exclusions = [entry for entry in await _mining_exclusions(cache)
                  if _normalize_text(entry.get("material")) == _normalize_text(result.material_name)]
    if not exclusions:
        return result
    result = deepcopy(result)
    groups = list(result.location_groups or [])
    if not groups and len(result.systems) == 1:
        groups = [MiningSystemLocations(system=result.systems[0], **{
            kind: list(getattr(result, kind)) for kind in MINING_LOCATION_TYPES
        })]
    for entry in exclusions:
        kind = entry.get("location_type")
        if kind not in MINING_LOCATION_TYPES:
            continue
        key = _mining_location_key(entry.get("location"))
        matched = False
        for group in groups:
            if _normalize_text(group.system) != _normalize_text(entry.get("system")):
                continue
            locations = getattr(group, kind)
            remaining = [location for location in locations if _mining_location_key(location) != key]
            matched = matched or len(remaining) != len(locations)
            locations[:] = remaining
        # Keep an aggregate location if another system still contains it.
        if matched and not any(_mining_location_key(location) == key for group in groups for location in getattr(group, kind)):
            locations = getattr(result, kind)
            locations[:] = [location for location in locations if _mining_location_key(location) != key]
    nonempty = [group for group in groups if any(getattr(group, kind) for kind in MINING_LOCATION_TYPES)]
    emptied = {_normalize_text(group.system) for group in groups if group not in nonempty}
    return replace(result, location_groups=nonempty,
                   systems=[system for system in result.systems if _normalize_text(system) not in emptied])


async def remove_community_mining_location(cache: SQLiteCache, entry: dict) -> bool:
    entries = await _community_mining_entries(cache)
    key = _normalize_text(entry.get("material"))
    material_entries = entries.get(key, [])
    remaining = [existing for existing in material_entries if not _same_community_mining_location(existing, entry)]
    if len(remaining) == len(material_entries):
        return False
    if remaining:
        entries[key] = remaining
    else:
        entries.pop(key, None)
    await cache.set(MINING_COMMUNITY_LOCATIONS_CACHE_KEY, entries, 315360000)
    return True


def _append_unique(values: list[str], value: str) -> None:
    if all(_normalize_text(existing) != _normalize_text(value) for existing in values):
        values.append(value)


async def apply_community_mining_locations(
    cache: SQLiteCache, result: MiningLocationResult,
) -> MiningLocationResult:
    entries = await _community_mining_entries(cache)
    material_entries = entries.get(_normalize_text(result.material_name), [])
    if not material_entries:
        return await _apply_mining_exclusions(cache, result)

    groups = deepcopy(list(result.location_groups or []))
    groups_by_system = {_normalize_text(group.system): group for group in groups}
    systems, lagrange_points = list(result.systems), list(result.lagrange_points)
    planets, moons = list(result.planets), list(result.moons)
    points_of_interest = list(result.points_of_interest)
    destinations = {
        "lagrange_points": lagrange_points, "planets": planets,
        "moons": moons, "points_of_interest": points_of_interest,
    }
    for entry in material_entries:
        if not isinstance(entry, dict):
            continue
        system = str(entry.get("system") or "").strip()
        kind = str(entry.get("location_type") or "").strip()
        location = str(entry.get("location") or "").strip()
        if not system or kind not in destinations or not location:
            continue
        system_key = _normalize_text(system)
        if system_key not in groups_by_system:
            group = MiningSystemLocations(system=system, lagrange_points=[], planets=[], moons=[], points_of_interest=[])
            groups_by_system[system_key] = group
            groups.append(group)
        display = f"{location} (Community)"
        _append_unique(getattr(groups_by_system[system_key], kind), display)
        _append_unique(systems, system)
        _append_unique(destinations[kind], display)

    values = dict(result.__dict__)
    values.update(systems=systems, lagrange_points=lagrange_points, planets=planets,
                  moons=moons, points_of_interest=points_of_interest, location_groups=groups)
    return await _apply_mining_exclusions(cache, MiningLocationResult(**values))
