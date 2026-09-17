from src.reputation import REPUTATION_PRIMARY_LADDERS


def test_primary_reputation_catalog_excludes_secondary_and_affinity_scopes() -> None:
    assert len(REPUTATION_PRIMARY_LADDERS) == 29
    assert "Master" in REPUTATION_PRIMARY_LADDERS["Covalex"]
    assert "Guild Steward" in REPUTATION_PRIMARY_LADDERS["Bounty Hunters Guild"]
    assert all("::" not in level for levels in REPUTATION_PRIMARY_LADDERS.values() for level in levels)
    assert all("Affinity" not in level for levels in REPUTATION_PRIMARY_LADDERS.values() for level in levels)


def test_primary_reputation_catalog_has_unique_givers_and_levels() -> None:
    assert len(REPUTATION_PRIMARY_LADDERS) == len(set(name.casefold() for name in REPUTATION_PRIMARY_LADDERS))
    assert all(len(levels) == len(set(level.casefold() for level in levels)) for levels in REPUTATION_PRIMARY_LADDERS.values())
