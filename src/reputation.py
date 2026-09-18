"""Star Citizen 4.10 reputation ladders and progress-card colors."""

REPUTATION_LADDERS: dict[str, tuple[str, ...]] = {
    "Covalex": ("Trainee", "Rookie", "Junior", "Member", "Experienced", "Senior", "Master"),
    "Headhunters": ("Applicant", "Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor", "Elite Contractor"),
    "Red Wind Linehaul": ("Trainee", "Rookie", "Junior", "Member", "Experienced", "Senior", "Master"),
    "Citizens For Prosperity": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Foxwell Enforcement": ("Neutral", "Member", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Shubin Interstellar": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Vaughn": ("Under Review", "Assassin In Training", "Tracker Trainee", "Low Level Assassin", "Assassin", "High Value Assassin", "Elite Assassin", "Master Assassin"),
    "United Wayfarers Club": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor", "Elite Contractor"),
    "Eckhart Security": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor", "Elite Contractor"),
    "Hurston Dynamics": ("Security Trainee", "Jr. Security Contractor", "Security Contractor", "Sr. Security Contractor", "Lead Security Contractor"),
    "Adagio Holdings": ("Neutral", "Jr. Contractor", "Contractor"),
    "Bit Zeros": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "microTech": ("Security Trainee", "Jr. Security Contractor", "Security Contractor", "Sr. Security Contractor", "Lead Security Contractor"),
    "Bounty Hunters Guild": ("Applicant", "Probationary Guild Member", "Junior Guild Member", "Guild Member", "Senior Guild Member", "Veteran Guild Member", "Guild Steward"),
    "Crusader Industries": ("Security Trainee", "Jr. Security Contractor", "Security Contractor", "Sr. Security Contractor", "Lead Security Contractor"),
    "FTL Courier": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Northrock Service Group": ("Applicant", "Neutral", "Senior Tracker", "Advanced Tracker"),
    "Dead Saints": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "ArcCorp": ("Security Trainee", "Jr. Security Contractor", "Security Contractor", "Sr. Security Contractor", "Lead Security Contractor"),
    "Ling Family Hauling": ("Trainee", "Rookie", "Junior", "Member", "Experienced", "Senior", "Master"),
    "Hockrow Agency": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Unified Distribution Management": ("Applicant", "Jr. Runner", "Runner"),
    "Recco Battaglia": ("Prospective Associate", "Associate", "Trusted Associate", "Prestige 1", "Prestige 2", "Prestige 3"),
    "InterSec Defense Solutions": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor", "Veteran Contractor", "Head Contractor"),
    "Rayari Incorporated": ("Neutral", "Jr. Contractor", "Contractor", "Sr. Contractor"),
    "Ruto": ("Applicant", "Security Trainee", "Jr. Security Contractor", "Security Contractor"),
    "Wildstar Racing": ("Racing Enthusiast", "Rookie Racer", "Racer", "Practiced Racer", "Experienced Racer", "Skilled Racer", "Dedicated Racer"),
    "Civilian Defense Force": ("Not Eligible", "Neutral"),
    "Covalex Independent Contractors": ("Neutral",),
    "Wikelo Emporium": ("Very Good Customer", "Very Best Customer"),
    "Highpoint Wilderness Specialists": ("Neutral",),
    'Tecia "Twitch" Pacheco': ("Neutral",),
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
