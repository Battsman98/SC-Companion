from src.reputation import REPUTATION_LADDERS, reputation_colors


def test_reputation_catalog_contains_all_current_ladders() -> None:
    assert len(REPUTATION_LADDERS) == 32
    assert "Master" in REPUTATION_LADDERS["Covalex"]
    assert "Guild Steward" in REPUTATION_LADDERS["Bounty Hunters Guild"]
    assert "Covalex Independent Contractors" in REPUTATION_LADDERS
    assert "Highpoint Wilderness Specialists" in REPUTATION_LADDERS
    assert 'Tecia "Twitch" Pacheco' in REPUTATION_LADDERS


def test_primary_reputation_catalog_has_unique_givers_and_levels() -> None:
    assert len(REPUTATION_LADDERS) == len(set(name.casefold() for name in REPUTATION_LADDERS))
    assert all(len(levels) == len(set(level.casefold() for level in levels)) for levels in REPUTATION_LADDERS.values())


def test_every_reputation_ladder_has_a_unique_two_color_ribbon() -> None:
    combinations = [reputation_colors(giver) for giver in REPUTATION_LADDERS]
    assert all(first != second for first, second in combinations)
    assert len(combinations) == len(set(combinations))
