"""Gating-risk heuristic — flag leads a NEW seller probably can't sell yet.

Amazon "gating" (selling restrictions) is ACCOUNT-SPECIFIC: whether a given seller
may list a given ASIN depends on that account's history, and the only authoritative
source is the seller's own Seller Central / SP-API ``getListingsRestrictions`` with
THEIR credentials. We cannot guarantee "ungated" for a buyer we don't control. What
we CAN do — for free, instantly, offline — is a calibrated RISK FLAG from data we
already capture (Amazon category + brand + hazmat keywords), so the lead list LEADS
with likely-open items and fences the likely-gated ones. The SP-API authoritative
check is a later opt-in upgrade (per-buyer, sold as a premium feature).

Most buyers of these lists are NEW sellers with little selling history, so we err
toward flagging: pushing a borderline item DOWN the list is the safe direction.

Pure (no I/O, no network) — fully unit-tested.

Output values map straight onto the leads columns:
  gating_status : "likely-open" | "likely-gated" | "unknown"   (leads.gating_status)
  hazmat        : "yes" | "no" | "unknown"                      (leads.hazmat)
"""

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Brand gating — brands that commonly require approval/invoices for new sellers.
# Lowercased. Matched against the structured brand first, else scanned in the
# title (word-boundary, length-guarded to avoid short-token false hits like "mac").
# --------------------------------------------------------------------------- #
GATED_BRANDS = {
    # apparel / footwear (brand-gated regardless of category)
    "nike", "adidas", "under armour", "the north face", "columbia", "vans",
    "converse", "new balance", "puma", "reebok", "crocs", "ugg", "lululemon",
    "carhartt", "patagonia", "timberland",
    # consumer electronics
    "apple", "sony", "samsung", "bose", "beats", "jbl", "sonos", "lg",
    "microsoft", "nintendo", "playstation", "xbox", "gopro", "dji", "logitech",
    "canon", "nikon", "fitbit", "garmin", "dyson", "razer", "anker",
    # toys / entertainment IP
    "lego", "disney", "hasbro", "mattel", "funko", "pokemon", "pokémon",
    "melissa & doug", "nerf", "barbie", "hot wheels",
    # beauty (premium beauty is heavily gated)
    "l'oreal", "l'oréal", "lancome", "lancôme", "estee lauder", "estée lauder",
    "clinique", "urban decay", "olaplex", "drunk elephant", "supergoop",
    "the ordinary", "tatcha", "charlotte tilbury", "fenty", "dior", "chanel",
    "mac cosmetics", "kerastase", "kérastase", "redken", "paul mitchell",
    # health / supplements (premium brands gate)
    "optimum nutrition", "garden of life", "ghost", "c4", "liquid i.v.",
    # home / kitchen / lifestyle
    "kitchenaid", "instant pot", "ninja", "yeti", "stanley", "hydro flask",
    "weber", "philips", "oral-b", "braun", "shark", "le creuset", "vitamix",
    "owala", "contigo",
    # accessories / luxury / optics
    "coach", "michael kors", "ray-ban", "ray ban", "oakley", "fossil",
    "casio", "seiko", "citizen",
}

# Brands worth scanning the title for need to be reasonably long to avoid
# substring collisions (e.g. "lg" in "bulge"). Brand-field matches are exempt.
_TITLE_SCAN_MIN = 4

# --------------------------------------------------------------------------- #
# Category gating — ordered (marker, risk). The Amazon breadcrumb is lowercased
# and the FIRST matching marker wins, checked high -> medium -> low (conservative:
# protect the buyer). Markers are substrings of a typical category path.
# --------------------------------------------------------------------------- #
_HIGH = [
    "grocery", "gourmet food", "beverage", "coffee", "tea",
    "beauty", "skin care", "skincare", "makeup", "cosmetic", "fragrance",
    "hair care", "premium beauty",
    "health", "personal care", "household health", "vitamin", "supplement",
    "nutrition", "wellness", "topical", "ointment", "medicine", "first aid",
    "baby", "watch", "jewelry", "jewellery", "fine art",
    "sexual wellness", "major appliance", "medical",
]
_MEDIUM = [
    "toys", "game", "collectible", "trading card", "pet", "shoe", "clothing",
    "apparel", "handbag", "luggage", "video game", "console", "automotive",
    "tire", "cell phone", "smartphone", "appliance", "industrial & scientific",
]
_LOW = [
    "home & kitchen", "kitchen", "tools", "home improvement", "office product",
    "office", "sports", "outdoor", "garden", "patio", "arts", "craft", "sewing",
    "book", "musical instrument", "electronics", "computer", "camera",
]

# --------------------------------------------------------------------------- #
# Hazmat — items that trigger Amazon's dangerous-goods review (their own selling
# restriction, separate from category gating). High-signal keywords only; bare
# "alcohol" is excluded (collides with "alcohol-free") in favour of specifics.
# --------------------------------------------------------------------------- #
_HAZMAT = [
    "aerosol", "hair spray", "hairspray", "spray sunscreen", "sunscreen spray",
    "spray paint", "flammable", "lithium", "li-ion", "lithium-ion", "battery",
    "batteries", "rubbing alcohol", "isopropyl", "ethanol", "peroxide",
    "acetone", "nail polish", "bleach", "ammonia", "propane", "butane",
    "compressed", "pressurized", "perfume", "cologne", "eau de", "fragrance",
    "hand sanitizer", "sanitizer", "disinfectant", "insecticide", "pesticide",
    "repellent", "fertilizer", "lighter fluid", "matches", "essential oil",
    "wd-40", "magnet",
]


@dataclass
class GatingAssessment:
    gating_status: str = "unknown"       # likely-open | likely-gated | unknown
    hazmat: str = "unknown"              # yes | no | unknown
    risk_score: float = 0.5              # 0 (open) .. 1 (gated); for intra-group sort
    reasons: list = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.gating_status == "likely-open"

    @property
    def is_gated(self) -> bool:
        return self.gating_status == "likely-gated"

    def note(self) -> str:
        """Short human-readable 'why', for the lead note/card. Empty if open/clean."""
        if not self.reasons:
            return ""
        icon = {"likely-gated": "🔒", "likely-open": "🔓", "unknown": "❔"}[self.gating_status]
        return f"{icon} {self.gating_status.replace('-', ' ')}: " + "; ".join(self.reasons)


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def brand_is_gated(brand=None, title=None):
    """(is_gated, matched_brand|None). Prefer the structured brand; if absent,
    scan the title for a gated brand (length-guarded, word-boundary)."""
    b = _norm(brand)
    if b:
        if b in GATED_BRANDS:
            return True, b
        for gb in GATED_BRANDS:
            if len(gb) >= 4 and gb in b:        # "nike, inc." contains "nike"
                return True, gb
        return False, None
    t = _norm(title)
    if not t:
        return False, None
    for gb in GATED_BRANDS:
        if len(gb) < _TITLE_SCAN_MIN:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(gb) + r"(?![a-z0-9])", t):
            return True, gb
    return False, None


def category_risk(category):
    """('high'|'medium'|'low'|'unknown', matched_marker|None) from the breadcrumb."""
    c = _norm(category)
    if not c:
        return "unknown", None
    for marker in _HIGH:
        if marker in c:
            return "high", marker
    for marker in _MEDIUM:
        if marker in c:
            return "medium", marker
    for marker in _LOW:
        if marker in c:
            return "low", marker
    return "unknown", None


def hazmat_flag(title=None, category=None):
    """('yes'|'no'|'unknown', matched_keyword|None). 'no' only when we had text to
    scan and found nothing; 'unknown' when there's nothing to judge from."""
    text = _norm((title or "") + " " + (category or ""))
    if not text.strip():
        return "unknown", None
    for kw in _HAZMAT:
        if kw in text:
            return "yes", kw
    return "no", None


def assess(title=None, brand=None, category=None) -> GatingAssessment:
    """Combine brand + category + hazmat signals into a gating risk flag.

    Rules (conservative — protect the new-seller buyer):
      likely-gated : brand-gated, OR high-risk category, OR hazmat
      likely-open  : low-risk category AND not brand-gated AND not hazmat
      unknown      : medium/unknown category with no other signal
    """
    reasons = []
    bgated, bmatch = brand_is_gated(brand, title)
    crisk, cmark = category_risk(category)
    hz, hzkw = hazmat_flag(title, category)

    score = 0.5
    if bgated:
        reasons.append(f"brand '{bmatch}' commonly gated")
        score += 0.4
    if crisk == "high":
        reasons.append(f"category '{cmark}' often approval-gated")
        score += 0.35
    elif crisk == "medium":
        score += 0.1
    elif crisk == "low":
        score -= 0.35
    if hz == "yes":
        reasons.append(f"hazmat signal '{hzkw}'")
        score += 0.25
    score = max(0.0, min(1.0, score))

    if bgated or crisk == "high" or hz == "yes":
        status = "likely-gated"
    elif crisk == "low":
        status = "likely-open"
        reasons = reasons or [f"category '{cmark}' usually open"]
    else:
        status = "unknown"

    return GatingAssessment(gating_status=status, hazmat=hz,
                            risk_score=round(score, 3), reasons=reasons)


# Lead-list ordering: open first, unknown next, gated last. Used by the generator
# to segment the deliverable so new-seller buyers see sellable items up top.
GATING_ORDER = {"likely-open": 0, "unknown": 1, "likely-gated": 2}
GATING_SECTIONS = [
    ("likely-open", "✅ Likely Open — no approval expected"),
    ("unknown", "❔ Unknown — verify in your account"),
    ("likely-gated", "🔒 Likely Gated — approval / ungating required"),
]


def gating_rank(status) -> int:
    return GATING_ORDER.get(status or "unknown", 1)
