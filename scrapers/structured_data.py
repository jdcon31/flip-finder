"""Structured-data product reader — the reliable price + identifier source.

Most retail PRODUCT pages embed schema.org/Product as JSON-LD
(<script type="application/ld+json">) and/or Open Graph / microdata meta tags.
Reading that gives the CANONICAL price, UPC/EAN (gtin), model (mpn/sku) and brand —
far more reliable than guessing the smallest price on a sale-grid card (which grabs
a financing "$13/mo" or an accessory price - a real source of false leads). This is
the same approach commercial sourcing tools use.

Pure parsing, fully unit-tested (no network, no bs4 — regex only, runs anywhere).
Pair with a detail-page fetch: the scanner finds candidate URLs on the sale grid,
then we read the product page's structured data before cross-referencing to Amazon
(UPC-first). The Amazon cross-reference itself is not part of this excerpt.
"""

import json
import re
from dataclasses import dataclass


@dataclass
class ProductData:
    name: str = None
    brand: str = None
    price: float = None
    list_price: float = None  # strikethrough / original price, when stated
    currency: str = None
    upc: str = None          # gtin13 / gtin12 / gtin / ean (digits only)
    model: str = None        # mpn, else sku
    weight_lb: float = None   # shipping weight in pounds, when stated
    availability: str = None
    image: str = None
    source: str = None       # "jsonld" | "meta" | None
    # "high" (canonical JSON-LD offer), "low" (sources disagree -> verify),
    # "medium" (meta only), None (no price)
    price_confidence: str = None

    @property
    def ok(self) -> bool:
        return self.price is not None or self.upc is not None


# --------------------------------------------------------------------------- #
# small value coercers
# --------------------------------------------------------------------------- #
def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def to_price(v):
    """'$1,299.00' / '329.99' / 329.99 -> float, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d[\d,]*\.?\d*", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _digits(v):
    d = re.sub(r"\D", "", str(v or ""))
    return d or None


def _brand_name(v):
    if isinstance(v, dict):
        return v.get("name")
    return v if isinstance(v, str) else None


def to_weight_lb(v):
    """schema.org weight -> pounds. Accepts a QuantitativeValue dict
    ({value, unitCode/unitText}) or a string like '2.5 lb' / '900 g'. None if
    unparseable. Defaults to pounds when no unit is given."""
    if v is None:
        return None
    if isinstance(v, dict):
        val = to_price(v.get("value"))
        unit = str(v.get("unitText") or v.get("unitCode") or "").lower()
    else:
        s = str(v)
        val = to_price(s)
        unit = s.lower()
    if val is None or val <= 0:
        return None
    if any(u in unit for u in ("kgm", "kilogram", "kg")):
        return round(val * 2.20462, 3)
    if "ounce" in unit or "onz" in unit or re.search(r"\boz\b", unit):
        return round(val / 16.0, 3)
    if "gram" in unit or "grm" in unit or re.search(r"\bg\b", unit):
        return round(val / 453.592, 3)
    return round(val, 3)  # lb / lbr / pound / unitless -> assume pounds


# --------------------------------------------------------------------------- #
# JSON-LD
# --------------------------------------------------------------------------- #
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)


def _iter_jsonld(html: str):
    """Yield every JSON object found in ld+json scripts, expanding @graph/arrays."""
    for block in _JSONLD_RE.findall(html or ""):
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            # some sites emit trailing commas / HTML entities; one light repair pass
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", block))
            except (json.JSONDecodeError, ValueError):
                continue
        for obj in _as_list(data):
            if isinstance(obj, dict) and "@graph" in obj:
                for g in _as_list(obj["@graph"]):
                    if isinstance(g, dict):
                        yield g
            elif isinstance(obj, dict):
                yield obj


def _is_product(obj: dict) -> bool:
    t = obj.get("@type")
    types = {str(x).lower() for x in _as_list(t)}
    return "product" in types


def price_from_offers(offers):
    """Pull (price, currency, list_price) from an Offer / AggregateOffer / list.

    Handles ``priceSpecification`` as a dict OR a list (schema.org allows both — some sites
    uses a list with a StrikethroughPrice entry). The selling price is the non-
    strikethrough spec; the StrikethroughPrice/MSRP becomes ``list_price``. For an
    AggregateOffer, falls back to lowPrice."""
    best_price, currency, list_price = None, None, None
    for off in _as_list(offers):
        if not isinstance(off, dict):
            continue
        currency = currency or off.get("priceCurrency")
        cand = off.get("price")
        for spec in _as_list(off.get("priceSpecification")):
            if not isinstance(spec, dict):
                continue
            sp = to_price(spec.get("price"))
            if sp is None:
                continue
            currency = currency or spec.get("priceCurrency")
            ptype = (spec.get("priceType") or "").lower()
            if any(t in ptype for t in ("strikethrough", "list", "msrp", "regular")):
                list_price = sp if (list_price is None or sp > list_price) else list_price
            elif cand is None:
                cand = sp                       # the actual selling price
        if cand is None:
            cand = off.get("lowPrice")
        p = to_price(cand)
        if p is not None and (best_price is None or p < best_price):
            best_price = p
    return best_price, currency, list_price


def parse_jsonld(html: str) -> ProductData:
    """Find the first schema.org/Product block and read its fields."""
    for obj in _iter_jsonld(html):
        if not _is_product(obj):
            continue
        price, currency, list_price = price_from_offers(obj.get("offers"))
        upc = (_digits(obj.get("gtin13")) or _digits(obj.get("gtin12"))
               or _digits(obj.get("gtin")) or _digits(obj.get("gtin8"))
               or _digits(obj.get("ean")))
        avail = obj.get("offers", {})
        availability = None
        for off in _as_list(avail):
            if isinstance(off, dict) and off.get("availability"):
                availability = str(off["availability"]).rsplit("/", 1)[-1]
                break
        image = obj.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")
        return ProductData(
            name=(obj.get("name") or "").strip() or None,
            brand=_brand_name(obj.get("brand")),
            price=price, list_price=list_price, currency=currency,
            upc=upc,
            model=(obj.get("mpn") or obj.get("sku") or None),
            weight_lb=to_weight_lb(obj.get("weight") or obj.get("shippingWeight")),
            availability=availability,
            image=image,
            source="jsonld",
        )
    return ProductData()


# --------------------------------------------------------------------------- #
# meta / microdata fallback
# --------------------------------------------------------------------------- #
def _content_for(html: str, key: str):
    """content="" of the first tag whose property/name/itemprop == key."""
    pat = re.compile(
        r'<[^>]*\b(?:property|name|itemprop)\s*=\s*["\']' + re.escape(key) + r'["\'][^>]*>',
        re.IGNORECASE)
    for tag in pat.findall(html or ""):
        m = re.search(r'\bcontent\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if m:
            return m.group(1).strip() or None
    return None


def parse_meta(html: str) -> ProductData:
    """Open Graph / microdata fallback when there's no JSON-LD Product.

    Only the *canonical* price tags (og:/product: meta) are used — NOT a bare
    itemprop="price", which on listing-heavy pages grabs the first related-product
    price (a real single-source misread)."""
    name = (_content_for(html, "og:title") or _content_for(html, "twitter:title"))
    price = to_price(_content_for(html, "product:price:amount")
                     or _content_for(html, "og:price:amount"))
    currency = (_content_for(html, "product:price:currency")
                or _content_for(html, "og:price:currency"))
    brand = _content_for(html, "product:brand") or _content_for(html, "brand")
    upc = (_digits(_content_for(html, "gtin13")) or _digits(_content_for(html, "gtin12"))
           or _digits(_content_for(html, "gtin")))
    model = _content_for(html, "mpn") or _content_for(html, "sku")
    image = _content_for(html, "og:image")
    has_any = any([name, price, brand, upc, model])
    return ProductData(name=name, brand=brand, price=price, currency=currency,
                       upc=upc, model=model, image=image,
                       source="meta" if has_any else None)


def extract_product(html: str) -> ProductData:
    """Best available product data: JSON-LD first, meta filling gaps, with a
    cross-validated price confidence so downstream can trust/flag/escalate.

    Confidence:
      * high   — canonical JSON-LD offer price (and any meta price agrees)
      * low    — JSON-LD and meta prices DISAGREE (>2%) -> verify before trusting
      * medium — price only from meta (no JSON-LD offer)
      * None   — no price found
    """
    pd = parse_jsonld(html)
    meta = parse_meta(html)
    jl_price, m_price = pd.price, meta.price
    # fill missing fields from meta (does not overwrite a JSON-LD price)
    for f in ("name", "brand", "price", "currency", "upc", "model", "image"):
        if getattr(pd, f) is None and getattr(meta, f) is not None:
            setattr(pd, f, getattr(meta, f))
    if pd.source is None:
        pd.source = meta.source

    if jl_price is not None and m_price is not None:
        agree = abs(jl_price - m_price) <= 0.02 * max(jl_price, m_price)
        pd.price_confidence = "high" if agree else "low"
        pd.price = jl_price                       # trust the canonical offer; flag if low
    elif jl_price is not None:
        pd.price_confidence = "high"
    elif m_price is not None:
        pd.price_confidence = "medium"
    return pd
