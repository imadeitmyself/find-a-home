from __future__ import annotations

# Tier 1: major chains — need Camoufox + residential proxy (proxy wired in when ready)
# Tier 2: mid-size chains — need Camoufox headless, no proxy
# Tier 3: small independents — plain urllib, no browser needed
#
# Until proxy credentials are configured (BROWSER_PROXY env var), Tier 1 falls back to
# Camoufox headless (same behaviour as Tier 2).

TIER_1_AGENTS = frozenset(
    [
        "Dexters",
        "Foxtons",
        "Hamptons",
        "KFH",
        "Knight Frank",
        "OpenRent",
        "Savills",
        "Winkworth",
    ]
)

TIER_2_AGENTS = frozenset(
    [
        "Akelius Residential",
        "Alex Crown Lettings & Estate Agents",
        "Andrew Lloyd Estates",
        "Austin Homes",
        "Bigmove Sales & Lettings",
        "Chancellors",
        "Chestertons",
        "Choice Homes",
        "David Astburys",
        "Dimension Estates",
        "Elkay Properties",
        "Ellis & Co",
        "Ernest-Brooks International",
        "Essential Living",
        "Felicity J. Lord",
        "Filey Properties",
        "Grainger",
        "Harris Brown",
        "Hello Neighbour",
        "Home Made",
        "Homefinders",
        "Hotblack Desiato",
        "Hunters",
        "IDM Estates",
        "Inner City Estates",
        "John D Wood & Co",
        "Keatons",
        "Knight Bishop",
        "Let UK Home / Letukhome",
        "Letio",
        "Maxwells Estates",
        "Neilson & Bauer",
        "Next Move",
        "Portico",
        "RentCityFlat",
        "Rosewood Estates",
        "Sandra Davidson Estate Agents",
        "Scraye",
        "Stirling Ackroyd",
        "Victor Michael",
        "Victorstone",
        "Zen Homes",
    ]
)


def tier_for_agent(agent_name: str) -> int:
    """Return the fetch tier (1, 2, or 3) for a given agent name.

    Handles both bare names ("Dexters") and names with postcode suffixes
    ("Dexters E9", "Dexters (E8/E9)").
    """
    name = agent_name.strip()
    if name in TIER_1_AGENTS:
        return 1
    if name in TIER_2_AGENTS:
        return 2
    # Check if name begins with a known agent (handles "Agent PostcodeArea" format)
    for t1 in TIER_1_AGENTS:
        if name.startswith(t1 + " ") or name.startswith(t1 + " ("):
            return 1
    for t2 in TIER_2_AGENTS:
        if name.startswith(t2 + " ") or name.startswith(t2 + " ("):
            return 2
    return 3
