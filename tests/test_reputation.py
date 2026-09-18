from src.reputation import (
    REPUTATION_LADDERS,
    canonical_reputation_giver,
    canonical_reputation_level,
    reputation_colors,
)


def test_reputation_catalog_contains_all_current_ladders() -> None:
    assert len(REPUTATION_LADDERS) == 29
    assert "Master" in REPUTATION_LADDERS["Covalex"]
    assert "Guild Steward" in REPUTATION_LADDERS["Bounty Hunters Guild"]
    assert "Master Technician" in REPUTATION_LADDERS["Aciedo Communications"]
    assert "Elite Security Contractor" in REPUTATION_LADDERS["Foxwell Enforcement"]
    assert "Master Tracker" in REPUTATION_LADDERS["Northrock Service Group"]
    assert "Tar Pits" in REPUTATION_LADDERS
    assert REPUTATION_LADDERS["XenoThreat"][-1] == "Rank VI"
    assert REPUTATION_LADDERS["Eckhart Security"] == (
        "Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor",
        "Veteran Contractor", "Head Contractor", "Elite Contractor",
    )


def test_affinity_and_dossier_only_contacts_are_not_submission_ladders() -> None:
    excluded = {
        "Civilian Defense Force", "Covalex Independent Contractors",
        "Highpoint Wilderness Specialists", "Rayari Incorporated", "Ruto",
        'Tecia "Twitch" Pacheco', "United Wayfarers Club",
    }
    assert excluded.isdisjoint(REPUTATION_LADDERS)


def test_corrected_primary_ladders_do_not_mix_rank_families() -> None:
    assert REPUTATION_LADDERS["Headhunters"] == REPUTATION_LADDERS["Eckhart Security"]
    assert REPUTATION_LADDERS["Unified Distribution Management"] == REPUTATION_LADDERS["Covalex"]
    assert "Tracker Trainee" not in REPUTATION_LADDERS["Vaughn"]
    assert REPUTATION_LADDERS["Wildstar Racing"][-1] == "Skilled Racer"
    assert REPUTATION_LADDERS["Wikelo Emporium"][0] == "New Customer"


def test_retired_eckhart_labels_map_to_primary_in_game_ladder() -> None:
    assert canonical_reputation_giver("Miles Eckhart") == "Eckhart Security"
    assert canonical_reputation_level("Miles Eckhart", "Security Contractor") == "Contractor"
    assert canonical_reputation_level("Eckhart Security", "Sr. Security Contractor") == "Sr. Contractor"


def test_primary_reputation_catalog_has_unique_givers_and_levels() -> None:
    assert len(REPUTATION_LADDERS) == len(set(name.casefold() for name in REPUTATION_LADDERS))
    assert all(len(levels) == len(set(level.casefold() for level in levels)) for levels in REPUTATION_LADDERS.values())


def test_every_reputation_ladder_has_a_unique_two_color_ribbon() -> None:
    combinations = [reputation_colors(giver) for giver in REPUTATION_LADDERS]
    assert all(first != second for first, second in combinations)
    assert len(combinations) == len(set(combinations))
