# models/filters.py
# ─────────────────────────────────────────────────────────────────────────────
# Shared pre-filter lists used by both the Adzuna scraper (to skip API noise
# before storing) and the ScoringAgent (to skip Claude calls for all sources).
#
# Keeping them in one place prevents drift between the two gatekeeping layers.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import re
from typing import Optional

# Job title must contain at least one of these to be considered relevant.
# Broad enough to catch all target roles; noise is trimmed by EXCLUDED_TITLE_KEYWORDS.
RELEVANT_TITLE_KEYWORDS: list[str] = [
    "engineer",
    "architect",
    "director",
    "manager",
    "principal",
    "staff",
    "lead",
    "head of",
    "vp",
    "vice president",
    "software",
    "platform",
    "cloud",
    "devops",
    "sre",
    "infrastructure",
    "solutions",
    "developer",
    # IoT / connected devices
    "iot",
    "embedded",
    "connected devices",
    "edge computing",
]

# Any title containing one of these is dropped even if it matched RELEVANT_TITLE_KEYWORDS.
# Order: most specific phrases first so substring matches are unambiguous.
EXCLUDED_TITLE_KEYWORDS: list[str] = [
    # Sales / business development
    "presales",
    "pre-sales",
    "pre sales",
    "sales manager",
    "sales representative",
    "sales engineer",
    "account manager",
    "account executive",
    "business development",
    # Non-tech management
    "property manager",
    "community manager",
    "leasing manager",
    "leasing",
    "project manager",
    "program manager",
    "office manager",
    "store manager",
    "branch manager",
    "operations manager",
    "business systems analyst",
    "fundraising",
    "transcription",
    "substation",
    # Non-software engineering & architecture disciplines
    "electrical engineer",
    "mechanical engineer",
    "civil engineer",
    "structural engineer",
    "chemical engineer",
    "landscape architect",
    "design specification",     # construction / building architect
    "facilities engineer",
    "maintenance engineer",
    "hydraulic engineer",
    "process engineer",
    "department lead",
    # Hospitality / non-IT domains
    "hotel",
    "hvac",
    "medical",
    # Junior / student roles
    "intern",
    "internship",
    "associate engineer",
    # Language-specific (too narrow for this profile)
    "java developer",
    "java engineer",
]

# Job description must contain at least one of these for the job to be sent to Claude.
# Acts as a universal tech-domain gate — short Adzuna snippets always name the domain.
# Deliberately avoids broad words like "technology" or "technical" that appear in
# non-IT job descriptions (HR tech, biotech, medical technology, etc.).
TECH_DESCRIPTION_KEYWORDS: list[str] = [
    # Languages & runtimes
    "software", "python", "javascript", "typescript", ".net", "golang", "rust", "java",
    # Cloud & infra
    "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "terraform", "ci/cd", "cicd",
    "infrastructure", "devops", "platform engineering",
    # APIs & architecture patterns
    "api", "microservice", "distributed system", "backend", "frontend", "full stack",
    "fullstack", "saas", "paas", "iaas", "application development",
    # Data & AI
    "data engineering", "data pipeline", "machine learning", "artificial intelligence",
    " ai ", "llm", "database",
    # Org / leadership (scoped tightly)
    "engineering team", "software engineer", "software development",
    "digital transformation",
    # IoT / embedded / edge
    "iot", "internet of things", "mqtt", "edge computing",
    "embedded", "connected devices", "industrial iot", "iiot",
    "device management", "telemetry", "firmware",
]

# ─── US state extraction ──────────────────────────────────────────────────────

_STATE_ABBREVS: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
})

# Sorted longest-first so multi-word states match before their substrings
# (e.g. "west virginia" before "virginia").
_STATE_NAMES: list[tuple[str, str]] = sorted(
    [
        ("alabama", "AL"), ("alaska", "AK"), ("arizona", "AZ"), ("arkansas", "AR"),
        ("california", "CA"), ("colorado", "CO"), ("connecticut", "CT"), ("delaware", "DE"),
        ("florida", "FL"), ("georgia", "GA"), ("hawaii", "HI"), ("idaho", "ID"),
        ("illinois", "IL"), ("indiana", "IN"), ("iowa", "IA"), ("kansas", "KS"),
        ("kentucky", "KY"), ("louisiana", "LA"), ("maine", "ME"), ("maryland", "MD"),
        ("massachusetts", "MA"), ("michigan", "MI"), ("minnesota", "MN"),
        ("mississippi", "MS"), ("missouri", "MO"), ("montana", "MT"), ("nebraska", "NE"),
        ("nevada", "NV"), ("new hampshire", "NH"), ("new jersey", "NJ"),
        ("new mexico", "NM"), ("new york", "NY"), ("north carolina", "NC"),
        ("north dakota", "ND"), ("ohio", "OH"), ("oklahoma", "OK"), ("oregon", "OR"),
        ("pennsylvania", "PA"), ("rhode island", "RI"), ("south carolina", "SC"),
        ("south dakota", "SD"), ("tennessee", "TN"), ("texas", "TX"), ("utah", "UT"),
        ("vermont", "VT"), ("virginia", "VA"), ("washington", "WA"),
        ("west virginia", "WV"), ("wisconsin", "WI"), ("wyoming", "WY"),
        ("district of columbia", "DC"),
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)

# County/parish/borough base-name → state.
# Only includes unambiguous names — county names shared by multiple states are omitted
# to avoid false positives (e.g. "Hamilton" appears in OH, TN, IN; "Montgomery" in 10+).
_COUNTY_STATE: dict[str, str] = {
    # Georgia (Atlanta metro)
    "fulton": "GA", "dekalb": "GA", "cobb": "GA", "gwinnett": "GA",
    "coweta": "GA", "muscogee": "GA",
    # Washington (Seattle / Puget Sound)
    "king": "WA", "pierce": "WA", "snohomish": "WA", "spokane": "WA",
    "thurston": "WA", "kitsap": "WA", "whatcom": "WA", "skagit": "WA",
    # Texas (Houston / DFW / Austin / San Antonio)
    "harris": "TX", "tarrant": "TX", "bexar": "TX", "travis": "TX",
    "collin": "TX", "denton": "TX", "fort bend": "TX", "hidalgo": "TX",
    # New Jersey
    "hudson": "NJ", "bergen": "NJ", "passaic": "NJ", "monmouth": "NJ",
    "ocean": "NJ", "atlantic": "NJ", "gloucester": "NJ",
    "burlington": "NJ", "cape may": "NJ", "hunterdon": "NJ",
    # Pennsylvania
    "allegheny": "PA", "bucks": "PA", "chester": "PA", "lancaster": "PA",
    "berks": "PA", "dauphin": "PA", "lehigh": "PA",
    "westmoreland": "PA", "luzerne": "PA", "lackawanna": "PA",
    # Connecticut
    "hartford": "CT", "new haven": "CT", "fairfield": "CT",
    "new london": "CT", "tolland": "CT", "litchfield": "CT",
    # Delaware
    "new castle": "DE",
    # New York (excl. ambiguous Nassau/Suffolk which also appear in FL/MA)
    "westchester": "NY", "onondaga": "NY", "albany": "NY",
    "saratoga": "NY", "rensselaer": "NY", "schenectady": "NY",
    "ulster": "NY", "dutchess": "NY", "rockland": "NY",
    # North Carolina (Charlotte / Raleigh / Triad)
    "mecklenburg": "NC", "wake": "NC", "guilford": "NC", "buncombe": "NC",
    "cabarrus": "NC", "iredell": "NC", "gaston": "NC", "new hanover": "NC",
    # Illinois (Chicago suburbs)
    "dupage": "IL", "will": "IL", "mchenry": "IL", "sangamon": "IL",
    # California
    "los angeles": "CA", "san diego": "CA", "riverside": "CA",
    "san bernardino": "CA", "santa clara": "CA", "alameda": "CA",
    "sacramento": "CA", "contra costa": "CA", "fresno": "CA",
    "san francisco": "CA", "san mateo": "CA", "kern": "CA",
    "ventura": "CA", "san joaquin": "CA", "santa barbara": "CA",
    "sonoma": "CA", "tulare": "CA", "solano": "CA", "stanislaus": "CA",
    "placer": "CA", "marin": "CA", "napa": "CA",
    # Florida
    "miami-dade": "FL", "broward": "FL", "palm beach": "FL",
    "hillsborough": "FL", "pinellas": "FL", "duval": "FL",
    "brevard": "FL", "volusia": "FL", "seminole": "FL",
    "pasco": "FL", "sarasota": "FL", "collier": "FL",
    "alachua": "FL", "manatee": "FL",
    # Virginia (Northern VA / Richmond)
    "fairfax": "VA", "prince william": "VA", "loudoun": "VA",
    "chesterfield": "VA", "henrico": "VA",
    # Maryland
    "anne arundel": "MD", "harford": "MD",
    # Massachusetts
    "worcester": "MA", "hampden": "MA", "barnstable": "MA",
    "norfolk": "MA", "plymouth": "MA",
    # Ohio (Cleveland / Toledo)
    "cuyahoga": "OH", "lorain": "OH", "mahoning": "OH", "lucas": "OH",
    # Michigan (Detroit metro / Grand Rapids / Ann Arbor)
    "oakland": "MI", "macomb": "MI", "washtenaw": "MI",
    "ingham": "MI", "kalamazoo": "MI",
    # Colorado (Denver metro / Fort Collins)
    "arapahoe": "CO", "boulder": "CO", "larimer": "CO", "weld": "CO",
    # Arizona (Phoenix / Tucson)
    "maricopa": "AZ", "pima": "AZ", "pinal": "AZ",
    "yavapai": "AZ", "coconino": "AZ",
    # Minnesota (Twin Cities)
    "hennepin": "MN", "ramsey": "MN", "anoka": "MN",
    "carver": "MN", "olmsted": "MN",
    # Tennessee (Nashville)
    "davidson": "TN", "maury": "TN",
    # Indiana (Lafayette)
    "tippecanoe": "IN",
    # Oregon (Portland)
    "multnomah": "OR", "clackamas": "OR",
    # Nevada
    "washoe": "NV",
    # Utah
    "salt lake": "UT",
    # Wisconsin (Madison)
    "dane": "WI", "waukesha": "WI",
    # South Carolina
    "greenville": "SC", "spartanburg": "SC", "horry": "SC",
    # Kansas
    "wyandotte": "KS", "sedgwick": "KS",
    # Louisiana
    "east baton rouge": "LA", "st. tammany": "LA",
    # New Mexico
    "bernalillo": "NM", "sandoval": "NM", "santa fe": "NM",
    # Idaho (Boise metro / CDA)
    "ada": "ID", "canyon": "ID", "kootenai": "ID",
    # Hawaii
    "honolulu": "HI", "maui": "HI", "kauai": "HI",
    # Vermont (Burlington area)
    "chittenden": "VT",
    # Nebraska (Omaha)
    "sarpy": "NE",
    # West Virginia
    "kanawha": "WV", "monongalia": "WV",
    # Kentucky (NKY / Lexington)
    "kenton": "KY",
    # Mississippi
    "hinds": "MS", "rankin": "MS",
    # Alabama
    "mobile": "AL",
    # New Hampshire
    "strafford": "NH",
    # Maine
    "penobscot": "ME",
    # Rhode Island
    "providence": "RI",
    # New Jersey — ambiguous nationally but dominant in NE tech-job context
    "middlesex": "NJ", "mercer": "NJ", "somerset": "NJ",
    "union": "NJ", "morris": "NJ", "camden": "NJ",
    # Pennsylvania (Philadelphia County is unique; Montgomery County PA vs MD — include PA)
    "philadelphia": "PA",
    # Georgia extras
    "bibb": "GA", "barrow": "GA", "henry": "GA", "walton": "GA",
    "walker": "GA", "paulding": "GA", "newton": "GA", "forsyth": "GA",
    # Houston County GA (Warner Robins area) — distinct from Harris County TX (city of Houston)
    "houston": "GA",
    # Texas extras
    "galveston": "TX", "wharton": "TX",
    # Minnesota (Twin Cities suburb)
    "dakota": "MN",
    # Essex County NJ dominant in job-search NE corridor (also MA, NY, VT, VA — minor)
    "essex": "NJ",
}

# City / borough name → state for locations that omit "County" or use borough names.
# Covers NYC boroughs (which appear bare in Ladders data) and major US tech hubs.
_CITY_STATE: dict[str, str] = {
    # NYC boroughs (appear without "County" in many scrapers)
    "manhattan": "NY", "brooklyn": "NY", "bronx": "NY",
    "queens": "NY", "staten island": "NY",
    # US tech hubs and major metros — unambiguous
    "san francisco": "CA", "palo alto": "CA", "mountain view": "CA",
    "sunnyvale": "CA", "cupertino": "CA", "santa clara": "CA",
    "san jose": "CA", "oakland": "CA", "berkeley": "CA", "fremont": "CA",
    "san mateo": "CA", "redwood city": "CA", "menlo park": "CA",
    "los angeles": "CA", "san diego": "CA", "irvine": "CA",
    "long beach": "CA", "anaheim": "CA", "sacramento": "CA",
    "seattle": "WA", "bellevue": "WA", "redmond": "WA", "kirkland": "WA",
    "renton": "WA", "everett": "WA", "tacoma": "WA", "spokane": "WA",
    "olympia": "WA", "bellingham": "WA",
    "boston": "MA", "cambridge": "MA", "worcester": "MA", "quincy": "MA",
    "chicago": "IL", "aurora": "IL", "rockford": "IL", "naperville": "IL",
    "houston": "TX", "san antonio": "TX", "dallas": "TX", "austin": "TX",
    "fort worth": "TX", "el paso": "TX", "arlington": "TX",
    "plano": "TX", "frisco": "TX", "mckinney": "TX", "irving": "TX",
    "denver": "CO", "colorado springs": "CO", "fort collins": "CO",
    "aurora": "CO", "boulder": "CO",
    "phoenix": "AZ", "tucson": "AZ", "mesa": "AZ", "chandler": "AZ",
    "scottsdale": "AZ", "gilbert": "AZ", "tempe": "AZ", "glendale": "AZ",
    "flagstaff": "AZ",
    "atlanta": "GA", "savannah": "GA", "augusta": "GA", "columbus": "GA",
    "macon": "GA", "athens": "GA", "alpharetta": "GA", "roswell": "GA",
    "miami": "FL", "tampa": "FL", "orlando": "FL", "jacksonville": "FL",
    "st. petersburg": "FL", "tallahassee": "FL", "fort lauderdale": "FL",
    "cape coral": "FL", "west palm beach": "FL", "clearwater": "FL",
    "gainesville": "FL",
    "charlotte": "NC", "raleigh": "NC", "greensboro": "NC",
    "durham": "NC", "winston-salem": "NC", "asheville": "NC",
    "wilmington": "NC", "fayetteville": "NC",
    "philadelphia": "PA", "pittsburgh": "PA", "allentown": "PA",
    "harrisburg": "PA", "reading": "PA", "erie": "PA",
    "new york": "NY", "buffalo": "NY", "rochester": "NY",
    "yonkers": "NY", "syracuse": "NY", "albany": "NY",
    "white plains": "NY", "new rochelle": "NY",
    "newark": "NJ", "jersey city": "NJ", "trenton": "NJ",
    "paterson": "NJ", "elizabeth": "NJ",
    "bridgeport": "CT", "new haven": "CT", "stamford": "CT",
    "hartford": "CT", "waterbury": "CT",
    "wilmington": "DE", "dover": "DE",
    "baltimore": "MD", "annapolis": "MD", "silver spring": "MD",
    "columbia": "MD", "rockville": "MD", "bethesda": "MD",
    "virginia beach": "VA", "norfolk": "VA", "chesapeake": "VA",
    "richmond": "VA", "arlington": "VA", "alexandria": "VA",
    "roanoke": "VA", "fredericksburg": "VA",
    "nashville": "TN", "memphis": "TN", "knoxville": "TN",
    "chattanooga": "TN", "clarksville": "TN",
    "indianapolis": "IN", "fort wayne": "IN",
    "columbus": "OH", "cleveland": "OH", "cincinnati": "OH",
    "toledo": "OH", "akron": "OH", "dayton": "OH",
    "detroit": "MI", "grand rapids": "MI", "ann arbor": "MI",
    "lansing": "MI", "flint": "MI",
    "minneapolis": "MN", "saint paul": "MN", "st. paul": "MN",
    "rochester": "MN", "duluth": "MN",
    "milwaukee": "WI", "madison": "WI", "green bay": "WI",
    "portland": "OR", "eugene": "OR", "salem": "OR", "bend": "OR",
    "beaverton": "OR", "hillsboro": "OR",
    "las vegas": "NV", "henderson": "NV", "reno": "NV",
    "salt lake city": "UT", "provo": "UT", "ogden": "UT",
    "boise": "ID", "nampa": "ID", "meridian": "ID",
    "omaha": "NE", "lincoln": "NE",
    "kansas city": "MO", "st. louis": "MO", "springfield": "MO",
    "new orleans": "LA", "baton rouge": "LA", "shreveport": "LA",
    "wichita": "KS", "overland park": "KS", "topeka": "KS",
    "des moines": "IA", "cedar rapids": "IA",
    "little rock": "AR",
    "jackson": "MS",
    "birmingham": "AL", "huntsville": "AL", "montgomery": "AL",
    "albuquerque": "NM", "santa fe": "NM", "las cruces": "NM",
    "louisville": "KY", "lexington": "KY",
    "charleston": "SC", "columbia": "SC",
    "anchorage": "AK",
    "honolulu": "HI",
    "providence": "RI",
    "burlington": "VT",
    "portland": "ME",
    "manchester": "NH", "concord": "NH", "nashua": "NH",
    "washington": "DC",
    # NJ cities that appear without state info
    "princeton": "NJ", "iselin": "NJ", "rahway": "NJ",
    "basking ridge": "NJ", "cherry hill": "NJ", "whippany": "NJ",
    "pennington": "NJ", "piscataway": "NJ", "metuchen": "NJ",
    "edison": "NJ", "new brunswick": "NJ", "east brunswick": "NJ",
    "parsippany": "NJ", "morristown": "NJ",
    # PA cities
    "king of prussia": "PA", "allentown": "PA", "bethlehem": "PA",
    # GA cities (beyond Atlanta metro already in dict)
    "warner robins": "GA", "stockbridge": "GA", "social circle": "GA",
    "rock spring": "GA", "rome": "GA", "dalton": "GA",
    # TX cities
    "the woodlands": "TX", "tiki island": "TX", "galveston": "TX",
    "sugar land": "TX", "pearland": "TX", "katy": "TX",
    # MN cities
    "sunfish lake": "MN", "eden prairie": "MN", "plymouth": "MN",
    "bloomington": "MN", "minnetonka": "MN",
    # NJ cities
    "short hills": "NJ", "west orange": "NJ", "montclair": "NJ",
    "hackensack": "NJ", "teaneck": "NJ",
}


def extract_us_state(location: Optional[str]) -> Optional[str]:
    """
    Returns a 2-letter US state abbreviation extracted from a location string,
    or None if no US state is recognisable.

    Handles common scraper formats:
      "Atlanta, GA"                → "GA"   (abbreviation)
      "Seattle, WA, United States" → "WA"   (abbreviation + suffix)
      "Austin, Texas"              → "TX"   (full state name)
      "Atlanta, Fulton County"     → "GA"   (county lookup)
      "Grand Central, Manhattan"   → "NY"   (city/borough lookup)
      "Remote"                     → None
    """
    if not location:
        return None
    loc = location.strip()

    # 1. Most common: ", XX" optionally followed by comma or end-of-string
    m = re.search(r",\s*([A-Z]{2})(?:\s*,|\s*$)", loc)
    if m and m.group(1) in _STATE_ABBREVS:
        return m.group(1)

    # 2. Full state name (longest-first to avoid "virginia" shadowing "west virginia")
    lower = loc.lower()
    for name, abbrev in _STATE_NAMES:
        if re.search(r"\b" + re.escape(name) + r"\b", lower):
            return abbrev

    # 3. Lone 2-letter uppercase token at end of string
    m = re.search(r"(?:^|,\s*)([A-Z]{2})$", loc)
    if m and m.group(1) in _STATE_ABBREVS:
        return m.group(1)

    # 4. County / Parish / Borough name — handles "Atlanta, Fulton County" etc.
    for cm in re.finditer(
        r"([A-Za-z][A-Za-z0-9 '\-]*?)\s+(?:County|Parish|Borough)\b", loc, re.IGNORECASE
    ):
        key = cm.group(1).strip().lower()
        if key in _COUNTY_STATE:
            return _COUNTY_STATE[key]

    # 5. City / borough name from comma-separated segments — handles
    #    "Grand Central, Manhattan" and "Nob Hill, San Francisco" etc.
    segments = [s.strip().lower() for s in loc.split(",")]
    for seg in segments:
        if seg in _CITY_STATE:
            return _CITY_STATE[seg]

    return None
