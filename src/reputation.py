"""Star Citizen 4.10 reputation ladders and progress-card colors."""

_CONTRACTOR = (
    "Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor",
    "Veteran Contractor", "Head Contractor", "Elite Contractor",
)
_SECURITY = (
    "Applicant", "Security Trainee", "Jr. Security Contractor", "Security Contractor",
    "Sr. Security Contractor", "Lead Security Contractor", "Elite Security Contractor",
)
_HAULING = ("Trainee", "Rookie", "Junior", "Member", "Experienced", "Senior", "Master")
_TECHNICIAN = (
    "Applicant", "Technician-in-Training", "Jr. Technician", "Technician",
    "Sr. Technician", "Master Technician",
)
_BOUNTY_TRACKER = (
    "Applicant", "Tracker Trainee", "Associate Tracker", "Tracker",
    "Advanced Tracker", "Senior Tracker", "Master Tracker",
)
_HIRED_MUSCLE = ("Applicant", "Rank I", "Rank II", "Rank III", "Rank IV", "Rank V", "Rank VI")

# One submission ladder per visible reputation contact. Affinity-only and
# dossier-only contacts are deliberately excluded: the tracker follows the
# primary rank progression that can be verified from the in-game Career view.
REPUTATION_LADDERS: dict[str, tuple[str, ...]] = {
    "Aciedo Communications": _TECHNICIAN,
    "Adagio Holdings": _CONTRACTOR,
    "ArcCorp": _SECURITY,
    "Bit Zeros": _CONTRACTOR,
    "Bounty Hunters Guild": (
        "Applicant", "Probationary Guild Member", "Junior Guild Member", "Guild Member",
        "Senior Guild Member", "Veteran Guild Member", "Guild Steward",
    ),
    "Citizens For Prosperity": _CONTRACTOR,
    "Covalex": _HAULING,
    "Crusader Industries": _SECURITY,
    "Dead Saints": _CONTRACTOR,
    "Eckhart Security": _CONTRACTOR,
    "Foxwell Enforcement": _SECURITY,
    "FTL Courier": _HAULING,
    "Headhunters": _CONTRACTOR,
    "Hockrow Agency": _CONTRACTOR,
    "Hurston Dynamics": _SECURITY,
    "InterSec Defense Solutions": _CONTRACTOR,
    "Klescher Rehabilitation Facilities": _TECHNICIAN,
    "Ling Family Hauling": _HAULING,
    "microTech": _SECURITY,
    "Northrock Service Group": _BOUNTY_TRACKER,
    "Recco Battaglia": (
        "Prospective Associate", "Associate", "Trusted Associate",
        "Prestige 1", "Prestige 2", "Prestige 3",
    ),
    "Red Wind Linehaul": _HAULING,
    "Shubin Interstellar": _CONTRACTOR,
    "Tar Pits": _CONTRACTOR,
    "Unified Distribution Management": _HAULING,
    "Vaughn": (
        "Under Review", "Assassin In Training", "Low Level Assassin", "Assassin",
        "High Value Assassin", "Elite Assassin", "Master Assassin",
    ),
    "Wikelo Emporium": ("New Customer", "Very Good Customer", "Very Best Customer"),
    "Wildstar Racing": (
        "Racing Enthusiast", "Novice Racer", "Rookie Racer", "Racer",
        "Practiced Racer", "Dedicated Racer", "Experienced Racer", "Skilled Racer",
    ),
    "XenoThreat": _HIRED_MUSCLE,
}

# Preserve progress approved before the catalog used the in-game faction name
# and primary Standing ladder. These aliases are intentionally limited to
# Eckhart; similarly named Security ranks remain valid for other factions.
_REPUTATION_GIVER_ALIASES = {
    "miles eckhart": "Eckhart Security",
}
_ECKHART_LEVEL_ALIASES = {
    "applicant": "Neutral",
    "security trainee": "Neutral",
    "jr. security contractor": "Jr. Contractor",
    "security contractor": "Contractor",
    "sr. security contractor": "Sr. Contractor",
    "lead security contractor": "Head Contractor",
}


def canonical_reputation_giver(giver: str) -> str | None:
    """Return the supported in-game giver name, accepting retired labels."""
    normalized = giver.strip().casefold()
    alias = _REPUTATION_GIVER_ALIASES.get(normalized)
    if alias:
        return alias
    return next((name for name in REPUTATION_LADDERS if name.casefold() == normalized), None)


def canonical_reputation_level(giver: str, level: str) -> str | None:
    """Return a level on the giver's current primary ladder."""
    canonical_giver = canonical_reputation_giver(giver)
    if canonical_giver is None:
        return None
    normalized = level.strip().casefold()
    if canonical_giver == "Eckhart Security":
        normalized = _ECKHART_LEVEL_ALIASES.get(normalized, level.strip()).casefold()
    return next(
        (name for name in REPUTATION_LADDERS[canonical_giver] if name.casefold() == normalized),
        None,
    )

# Pairing colors instead of assigning Discord roles keeps the visual identity
# scalable. With eight colors there are 56 ordered combinations, enough for
# every current ladder while keeping each giver's ribbon unique and stable.
_RIBBON_COLORS = (
    "#22d3ee", "#3b82f6", "#8b5cf6", "#ec4899",
    "#ef4444", "#f59e0b", "#84cc16", "#14b8a6",
)


def reputation_colors(giver: str) -> tuple[str, str]:
    try:
        index = tuple(REPUTATION_LADDERS).index(giver)
    except ValueError:
        index = sum(ord(character) for character in giver)
    primary = index % len(_RIBBON_COLORS)
    secondary = (index // len(_RIBBON_COLORS) + primary + 1) % len(_RIBBON_COLORS)
    return _RIBBON_COLORS[primary], _RIBBON_COLORS[secondary]
