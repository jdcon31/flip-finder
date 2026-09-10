"""Smart pre-filter — cheap rules that run BEFORE the expensive Amazon lookup.

Pure functions, no I/O. Implements PLAN.md "Smart Pre-Filtering": drop obvious
non-candidates so we don't waste Amazon cross-references on them. This is the
*rule-based* gate; the trained Skill-2 vision model (TRAINING.md) layers on top
later to skip more aggressively.

Rules (PLAN.md):
  - source price < $8        -> skip (margin too thin after fees)
  - discount < 20% off       -> skip (unlikely profitable)
  - sized clothing (S/M/L..) -> skip (size-specific apparel rarely resells)
  - no recognizable brand    -> keep, but flag as lower priority
"""

import re

MIN_SOURCE_PRICE = 8.0
MIN_DISCOUNT_PCT = 20.0

# Size tokens. NOTE: bare single letters (S/M/L) are excluded — they match
# possessives ("Men's") and stray initials.
_SIZE_PATTERN = re.compile(
    r"\b(?:XS|XL|XXL|XXXL|2XL|3XL|4XL|small|medium|large|x-?large|size\s*\d{1,2})\b",
    re.IGNORECASE,
)
# The size rule only matters for CLOTHING (sizes make apparel hard to resell).
# A "Large" dog toy or "Medium" roast is fine — require an apparel word too.
_APPAREL_PATTERN = re.compile(
    r"\b(shirt|tee|t-shirt|hoodie|sweatshirt|sweater|pants?|trousers?|leggings?|"
    r"jeans?|shorts?|dress|skirt|jacket|coat|vest|socks?|briefs?|boxers?|bra|tank|"
    r"polo|jersey|tights|pullover|cardigan|blouse|romper|jumpsuit|joggers?|"
    r"sweatpants?|underwear|swimsuit|bikini|trunks|robe|apparel|clothing)\b",
    re.IGNORECASE,
)


def looks_sized_apparel(title: str) -> bool:
    """True only when a title is BOTH apparel and size-specific (hard to resell)."""
    t = title or ""
    return bool(_SIZE_PATTERN.search(t)) and bool(_APPAREL_PATTERN.search(t))


# --------------------------------------------------------------------------- #
# title <-> URL consistency — catch EXTRACTOR mis-pairing (title from one product
# card, link/price from another). A real card's URL slug is built from its name,
# so a correct pairing shares most slug words with the title; a mis-pair shares
# almost none (just brand/department). Store-agnostic: catches the bug whether the
# generic OR the selector extractor produced it.
# --------------------------------------------------------------------------- #
# Department / structural words that match across unrelated products — excluded so
# overlap reflects the actual PRODUCT, not "womens" or "nike" alone.
_NOISE_TOKENS = {
    "the", "and", "for", "with", "from", "your", "our", "this", "that",
    "womens", "women", "mens", "men", "kids", "kid", "junior", "juniors",
    "girls", "boys", "girl", "boy", "unisex", "adult", "new", "size", "sizes",
    "plus", "pack", "count", "set", "piece", "pieces", "assorted", "color", "colors",
}
# URL path words that are structure, not product name.
_URL_NOISE = {
    "product", "products", "prod", "prd", "dp", "item", "items", "shop", "sale",
    "clearance", "jsp", "html", "htm", "aspx", "php", "www", "com", "catalog",
    "pages", "collections", "default", "page",
}


def _sig_tokens(text: str, extra_noise=frozenset()) -> set:
    toks = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and not t.isdigit()
            and t not in _NOISE_TOKENS and t not in extra_noise}


def _slug_tokens(url: str) -> set:
    from urllib.parse import urlparse
    path = urlparse(url or "").path
    return _sig_tokens(path.replace("/", " "), extra_noise=_URL_NOISE)


def title_matches_url(title: str, url: str, min_coverage: float = 0.4) -> bool:
    """True if the title plausibly describes the product the URL points to.

    Measures what fraction of the URL slug's significant words appear in the title
    (slug-coverage, so it's robust to long marketing titles). Returns True when it
    CAN'T judge (no usable slug — e.g. an id-only or /dp/ASIN URL) so we never drop
    on missing evidence."""
    slug = _slug_tokens(url)
    if len(slug) < 2:
        return True                       # nothing to check against -> don't drop
    title_toks = _sig_tokens(title)
    if not title_toks:
        return True
    shared = slug & title_toks
    return (len(shared) / len(slug)) >= min_coverage


def pre_filter(product: dict, *, min_price: float = MIN_SOURCE_PRICE,
               min_discount_pct: float = MIN_DISCOUNT_PCT, known_brands=None) -> dict:
    """Decide whether a scanned product is worth an Amazon lookup.

    ``product`` keys used: ``sale_price`` (float), ``discount_pct`` (float|None),
    ``name`` (str), ``brand`` (str|None). Returns
    ``{"worth_checking": bool, "reason": str, "flags": [..]}``.
    """
    name = product.get("name") or ""
    sale = product.get("sale_price")
    discount = product.get("discount_pct")
    brand = (product.get("brand") or "").strip()
    flags = []

    if sale is None:
        return {"worth_checking": False, "reason": "no sale price", "flags": flags}
    if sale < min_price:
        return {"worth_checking": False,
                "reason": f"price ${sale:.2f} < ${min_price:.2f}", "flags": flags}
    if looks_sized_apparel(name):
        return {"worth_checking": False, "reason": "sized apparel", "flags": flags}
    if discount is not None and discount < min_discount_pct:
        return {"worth_checking": False,
                "reason": f"discount {discount:.0f}% < {min_discount_pct:.0f}%", "flags": flags}
    # Extractor sanity: the title must actually describe the linked product. A
    # mismatch means the scraper paired this URL/price with a NEIGHBOUR's title —
    # cross-referencing it would mint a wrong-item lead (the Aeropostale-jean /
    # SO-jogger class of bug). Drop before we waste an Amazon look-up on garbage.
    url = product.get("url")
    if url and name and not title_matches_url(name, url):
        return {"worth_checking": False,
                "reason": "title/URL mismatch — likely extractor mis-pair", "flags": flags}

    # Brand recognition lowers priority but does not skip.
    if known_brands is not None:
        recognized = brand and brand.lower() in {b.lower() for b in known_brands}
        if not recognized:
            flags.append("unrecognized_brand")

    return {"worth_checking": True, "reason": "passed", "flags": flags}
