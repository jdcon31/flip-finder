"""Amazon referral and closing fee tables (FBM).

Pure functions, no I/O. Rates sourced from PLAN.md
section "Category-Specific Referral Fees". Most categories are a flat 15%;
a handful are tiered by sell price. We model every category the same way —
as an ordered list of (upper_limit, rate) breakpoints — so the resolver is
generic. For sell_price <= upper_limit, the corresponding rate applies; the
final breakpoint uses infinity to catch everything above.
"""

INF = float("inf")

# Each category maps to a list of (upper_price_limit, rate) breakpoints,
# sorted ascending by limit. Rate is a fraction (0.15 == 15%).
DEFAULT_RATE = 0.15

REFERRAL_FEES = {
    # 8% at or under $10, 15% above
    "baby": [(10.0, 0.08), (INF, 0.15)],
    "beauty": [(10.0, 0.08), (INF, 0.15)],
    "health": [(10.0, 0.08), (INF, 0.15)],
    # Grocery & Gourmet: 8% at or under $15, 15% above
    "grocery": [(15.0, 0.08), (INF, 0.15)],
    # Apparel was raised to 17%
    "clothing": [(INF, 0.17)],
    "apparel": [(INF, 0.17)],
    # 15% at or under $100, 8% above
    "electronics accessories": [(100.0, 0.15), (INF, 0.08)],
    # 15% at or under $200, 10% above
    "furniture": [(200.0, 0.15), (INF, 0.10)],
    # 20% at or under $250, 5% above
    "jewelry": [(250.0, 0.20), (INF, 0.05)],
}

# Media categories carry a flat per-item closing fee.
MEDIA_CATEGORIES = {"books", "dvd", "music", "software", "video games"}
CLOSING_FEE = 1.80


def _normalize(category: str) -> str:
    return (category or "").strip().lower()


# Map an Amazon category breadcrumb (e.g. "Beauty & Personal Care > Makeup") to
# the referral-fee category key, so the fee is the item's ACTUAL Amazon rate, not
# the source store's guess. Beauty/Health/Grocery are the 8%-under-threshold
# categories — the OA sweet spot — so capturing them is what surfaces those leads.
_FEE_CATEGORY_MARKERS = [
    ("grocery", ("grocery", "gourmet food", "pantry", "snack food")),
    ("beauty", ("beauty", "personal care", "makeup", "cosmetic", "skin care", "fragrance")),
    ("health", ("health & household", "health and household", "vitamin",
                "supplement", "nutrition", "household health")),
    ("baby", ("baby products",)),
    ("jewelry", ("jewelry", "jewellery")),
    ("furniture", ("furniture",)),
]


def amazon_fee_category(amazon_category: str):
    """Resolve an Amazon breadcrumb to a referral-fee category key, or None if it
    doesn't clearly map (caller then falls back to the store guess / default)."""
    c = (amazon_category or "").lower()
    if not c:
        return None
    for key, markers in _FEE_CATEGORY_MARKERS:
        if any(m in c for m in markers):
            return key
    return None


def referral_rate(category: str, sell_price: float) -> float:
    """Return the referral fee *rate* (fraction) for a category at a price."""
    breakpoints = REFERRAL_FEES.get(_normalize(category))
    if not breakpoints:
        return DEFAULT_RATE
    for limit, rate in breakpoints:
        if sell_price <= limit:
            return rate
    # Should be unreachable because the last limit is INF, but be safe.
    return breakpoints[-1][1]


def referral_fee(category: str, sell_price: float) -> float:
    """Return the referral fee in dollars for a category at a sell price."""
    return sell_price * referral_rate(category, sell_price)


def closing_fee(category: str) -> float:
    """Return the media closing fee ($1.80) for media categories, else $0."""
    return CLOSING_FEE if _normalize(category) in MEDIA_CATEGORIES else 0.0
