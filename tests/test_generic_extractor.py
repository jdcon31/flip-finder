"""Browser test: the generic extractor must bind a card's TITLE to its own product
link, never a neighbour's longer text (the wrong-item-lead root cause).

Uses a headless Chromium with crafted HTML. Skips cleanly if Playwright/Chromium
isn't available (e.g. on the global-python test run).

Run (in the venv):  python -m pytest tests/test_generic_extractor.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A grid where the naive "longest text in the card" heuristic would mis-pair: the
# first card's real title ("Blue Widget") is SHORTER than a promo link beside it.
_HTML = """
<html><body>
  <div class="grid">
    <div class="card">
      <a href="https://shop.com/p/blue-widget">Blue Widget</a>
      <div class="price">$19.99</div>
      <div class="orig">$39.99</div>
      <a href="https://shop.com/promo/mega">Super Premium Deluxe Mega Bundle Special Offer Today Only</a>
    </div>
    <div class="card">
      <a href="https://shop.com/p/red-gadget"><img alt="Red Gadget Pro Max Edition" src="x.jpg"></a>
      <div class="price">$25.00</div>
      <div class="orig">$50.00</div>
    </div>
  </div>
</body></html>
"""


def _extract(html):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright not installed")
    from scrapers.generic_extractor import extract_generic
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not available")
        try:
            page = browser.new_page()
            page.set_content(html)
            return extract_generic(page)
        finally:
            browser.close()


def test_title_binds_to_own_product_link_not_neighbour():
    products = _extract(_HTML)
    by = {p["url"]: p for p in products if p.get("url")}
    widget = next(p for p in products if "blue-widget" in (p.get("url") or ""))
    # The bug minted "Super Premium Deluxe…" as the title; the fix keeps "Blue Widget".
    assert widget["name"] == "Blue Widget"
    assert widget["sale_price"] == 19.99
    assert widget["original_price"] == 39.99


def test_image_only_link_uses_its_alt_text():
    products = _extract(_HTML)
    gadget = next(p for p in products if "red-gadget" in (p.get("url") or ""))
    # the product link is image-only -> title comes from that img's alt
    assert gadget["name"] == "Red Gadget Pro Max Edition"
