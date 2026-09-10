"""AI-assisted product extraction — the self-healing sourcing layer.

The pure-heuristic `generic_extractor` has a PRECISION problem: it grabs anything with
a price + a link, so category tiles ("Washer, dryer"), promo banners, and nav slip in
(one audited store returned "New Arrivals"/"Your cart is empty" as products).
This layer keeps the heuristic's candidates (its title-binding is good) and adds an LLM's
PRECISION: a local model decides, per row, whether it's a single real product for sale.

Design that keeps it trustworthy and cheap:
  * The heuristic (generic_extractor) supplies the candidates WITH names/prices/urls; the
    model ONLY decides keep-vs-drop per row. It never renames or invents anything — so it
    can't fabricate a price/link or mangle a title (letting an 8B model rewrite titles was
    tried and made them worse, e.g. "Blush Tint+3"). Every field is the heuristic's.
  * Runs on a local Ollama model (free, no API key) — default llama3.1:8b. Swappable.
  * Small, structured context (just name+price per candidate) so an 8B model handles it fast.
  * FAIL-OPEN: any model/parse error keeps ALL candidates (heuristic behaviour) rather than
    dropping products — junk that slips through is harmless (won't match on Amazon).

This is the "agents maintain the stores" layer: it reasons about the page fresh each run,
so it survives layout changes that break fixed selectors — no per-store rules to maintain.
"""

import json
import os
import re

from scrapers import generic_extractor

# Local Ollama model used for the keep/drop decision. Override with FF_TEXT_MODEL.
DEFAULT_MODEL = os.environ.get("FF_TEXT_MODEL", "llama3.1:8b")

_PROMPT = """A retail store's SALE/DEALS page produced this numbered list of candidate rows \
(name + price). Some are real individual products for sale; many are NOT — category tiles \
("Laptops", "Washer, dryer"), department links, "Shop all", filters, sort options, promo \
banners, shipping/return notices, "Your cart is empty", "New Arrivals", review counts, or \
price-range facets.

Return STRICT JSON {"keep":[<indices of the REAL products>]}.
- Keep a row ONLY if it names ONE specific purchasable product (brand/model/item, not a category).
- Drop navigation, categories, banners, empty-state text, and anything without a real product name.
- Use only the indices shown; do not invent rows.

Rows:
{items}
"""


def _call_ollama(prompt, model):
    import ollama
    resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}],
                       format="json", options={"temperature": 0})
    return resp["message"]["content"]


def _parse_keep(raw, n):
    """Parse {"keep":[...]} into a set of valid indices. Returns None on failure so the
    caller can fail-open (keep everything)."""
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw or "", re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    keep = data.get("keep") if isinstance(data, dict) else data
    if not isinstance(keep, list):
        return None
    return {i for i in keep if isinstance(i, int) and 0 <= i < n}


def ai_filter_products(products: list, page_title: str = "", model: str = DEFAULT_MODEL) -> list:
    """Given heuristic product dicts, drop the ones that aren't real products (categories,
    nav, empty-state, promos). Fail-open: returns the input unchanged on any model error."""
    if not products:
        return products
    items = "\n".join(
        f'{i}. {str(p.get("name"))[:90]!r}  ${p.get("sale_price")}'
        for i, p in enumerate(products))
    try:
        raw = _call_ollama(_PROMPT.replace("{items}", items), model)
    except Exception:
        return products                                   # fail-open: no model -> keep all
    keep = _parse_keep(raw, len(products))
    if keep is None:
        return products                                   # unparseable -> keep all
    return [p for i, p in enumerate(products) if i in keep]


def extract_products_ai(page, model: str = DEFAULT_MODEL) -> list:
    """Heuristic-extract the current page, then AI-filter out the non-products.
    Same dict shape as generic_extractor.extract_generic."""
    products = generic_extractor.extract_generic(page)
    title = ""
    try:
        title = page.title()
    except Exception:
        pass
    return ai_filter_products(products, page_title=title, model=model)
